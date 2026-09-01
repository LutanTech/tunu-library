import os, re, base64, requests, traceback
from datetime import datetime,timedelta
from sqlalchemy import or_
from flask import Flask,render_template,request,redirect,url_for,abort,g,make_response,jsonify,flash,send_from_directory, session
from models import db,User,LibraryBook,Reservation,Borrowing,Payment,Notification,AdminPermission,BookFlag,Book
from utils import *
from emails import mail
from notifications import notify,unread, unreadCount
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google.auth import default
from google_auth_oauthlib.flow import Flow

app=Flask(__name__)
migrate = Migrate(app, db)
COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE","0")=="1"
app.config.update(
    MAX_CONTENT_LENGTH=25*1024*1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=72),
    SECRET_KEY=os.getenv("FLASK_SECRET","secret-key"),
    SQLALCHEMY_DATABASE_URI="sqlite:///tunu_library.db",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    MAIL_PORT=465,
    MAIL_USE_SSL=True,
    MAIL_SERVER="mail.tunupublishers.com",
    MAIL_USERNAME = "library@tunupublishers.com",
    MAIL_PASSWORD = os.getenv("MAIL_PASS"),
)
SENDER_MAIL = "library@tunupublishers.com"


app.config["MAIL_DEFAULT_SENDER"] = (
    "Tunu Library",
     f'{SENDER_MAIL}'
)

limiter = Limiter(
    key_func = get_remote_address,
    app=app,
    storage_uri = "memory://",
    default_limits=["20 per minute"]
)


CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
SHORTCODE = os.getenv("MPESA_SHORTCODE")
PASSKEY = os.getenv("MPESA_PASSKEY")
BASE_URL = "https://sandbox.safaricom.co.ke" if os.getenv("MPESA_ENV", "sandbox") == "sandbox" else "https://api.safaricom.co.ke"
MEMBERSHIP_FEE_BOB=100
BORROWING_FEE_BOB=100
BORROWING_WINDOW_DAYS=90
PICKUP_WAIT_DAYS=2
GOOGLE_CLIENT_SECRETS_FILE = "client_secret.json"


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile"
]


os.environ["OAUTHLIB_INSECURE_TRANSPORT"]="1"
db.init_app(app)
mail.init_app(app)

@app.before_request
def load_user():
    g.user=None
    p=verify_session(request.cookies.get("tunu_session"))
    if p:g.user=db.session.get(User,p["user_id"])
    requested_language=request.args.get("lang")
    cookie_language=request.cookies.get("tunu_language")
    g.language=requested_language or cookie_language or "en"

@app.context_processor
def globals():
    user = getattr(g, "user", None)
    return {
        "current_user": user,
        "unread_notifications": unread(user.id) if user else None,
        "unread_count": unreadCount(user.id) if user else 0,
        "language": getattr(g, "language", "en")
    }

def auth():
    if not g.user:return redirect(url_for("login",next=request.path))

def format_phone(num):
    if not num: return None
    num = re.sub(r"\D", "", str(num))
    return f"254{num[1:]}" if num.startswith("0") else (f"254{num}" if num.startswith(("7", "1")) else num)

def mpesa_is_configured():
    return all([CONSUMER_KEY, CONSUMER_SECRET, SHORTCODE, PASSKEY])


def get_access_token():
    if not mpesa_is_configured():
        raise RuntimeError("M-Pesa credentials are not configured.")
    res = requests.get(f"{BASE_URL}/oauth/v1/generate?grant_type=client_credentials", auth=(CONSUMER_KEY, CONSUMER_SECRET), timeout=20)
    res.raise_for_status()
    data = res.json()
    if not data.get("access_token"):
        raise RuntimeError("M-Pesa did not return an access token.")
    return data["access_token"]

def normalize_phone(phone):
    phone="".join(filter(str.isdigit,str(phone or "")))

    if phone.startswith("0"):
        phone="254"+phone[1:]
    elif phone.startswith("7"):
        phone="254"+phone
    elif phone.startswith("254") and len(phone)==12:
        pass
    else:
        raise ValueError("Invalid Kenyan phone number")

    if len(phone)!=12 or not phone.startswith("2547"):
        raise ValueError("Invalid Kenyan phone number")

    return phone

def initiate_mpesa_payment(amount,phone,callback_url,account_reference):
    phone=normalize_phone(phone)

    timestamp=datetime.now().strftime("%Y%m%d%H%M%S")

    password=base64.b64encode(
        f"{SHORTCODE}{PASSKEY}{timestamp}".encode()
    ).decode()

    token=get_access_token()

    payload={
        "BusinessShortCode":str(SHORTCODE),
        "Password":password,
        "Timestamp":timestamp,
        "TransactionType":"CustomerPayBillOnline",
        "Amount":int(amount),
        "PartyA":phone,
        "PartyB":str(SHORTCODE),
        "PhoneNumber":phone,
        "CallBackURL":callback_url,
        "AccountReference":str(account_reference),
        "TransactionDesc":"Library Fee"
    }

    headers={
        "Authorization":f"Bearer {token}",
        "Content-Type":"application/json"
    }

    response=requests.post(
        f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
        json=payload,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    return response.json()

def admin(permission=None):
    if not g.user:abort(401)
    if not g.user.is_admin:abort(403)
    if permission and not has_permission(g.user,permission):abort(403)

def login_response(user):
    next_url=request.args.get("next", "")
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        next_url=url_for("dashboard")
    r=make_response(redirect(next_url))
    r.set_cookie("tunu_session",create_session(user.id),httponly=True,secure=COOKIE_SECURE,samesite="Lax",max_age=SESSION_DURATION,path="/")
    return r

@app.route("/")
def index():
    return render_template("index.html",books=get_books().get("books",[])[:8])

@app.route("/books")
def books():
    page=request.args.get("page",1,type=int)
    search=request.args.get("q",request.args.get("search","")) .strip()
    grade=request.args.get("grade","").strip()
    status=request.args.get("status","").strip()
    data=get_books(page,search,grade,status)
    return render_template("books.html",books=data.get("books",[]),pagination=data.get("pagination",{}),labels=data.get("labels",[]),search=search,grade=grade,status=status)

@app.route("/books/<book_id>")
def book(book_id):
    item=find_book(book_id)
    if not item:abort(404)
    is_member=False
    is_already_borrowed=False
    is_already_requested=False
    is_already_reserved=False
    if g.user:
        is_already_borrowed=Borrowing.query.filter_by(user_id=g.user.id,book_id=book_id,status="borrowed").first() is not None
        is_already_requested=Borrowing.query.filter(Borrowing.user_id==g.user.id,Borrowing.book_id==book_id,Borrowing.status.in_(["requested","pending"])).first() is not None
        is_already_reserved=Reservation.query.filter(Reservation.user_id==g.user.id,Reservation.book_id==book_id,Reservation.status.in_(["pending","approved"])).first() is not None

        is_member = g.user.membership_expires and g.user.membership_expires > datetime.utcnow() 
    return render_template("book_detail.html",book=item,library=LibraryBook.query.filter_by(book_id=book_id).first(),is_already_borrowed=is_already_borrowed,is_already_requested=is_already_requested,is_already_reserved=is_already_reserved,membership_fee_bob=MEMBERSHIP_FEE_BOB,borrowing_fee_bob=BORROWING_FEE_BOB,borrowing_window_days=BORROWING_WINDOW_DAYS,pickup_wait_days=PICKUP_WAIT_DAYS, is_member=is_member)

@app.route("/books/<book_id>/flag",methods=["POST"])
def flag_book(book_id):
    x=auth()
    if x:return x
    book=find_book(book_id)
    if not book:abort(404)
    category=request.form.get("category","other").strip().lower()
    reason=request.form.get("reason","").strip()
    details=request.form.get("details","").strip()
    if category not in {"damaged","missing_pages","incorrect_details","availability","other"}:
        category="other"
    if not reason:
        flash("Please provide a reason for the book flag.","warning")
        return redirect(url_for("book",book_id=book_id))
    flag=BookFlag(book_id=book_id,user_id=g.user.id,category=category,reason=reason[:500],details=details or None)
    db.session.add(flag)
    db.session.commit()
    for user in User.query.filter_by(is_admin=True).all():
        notify(user.id,"Book flag submitted",f"{g.user.username} flagged {book['title']}: {reason[:120]}")
    flash("Thank you. Your book flag has been sent to the library team.","success")
    return redirect(url_for("book",book_id=book_id))

@app.route("/register",methods=["GET","POST"])
@limiter.limit("5 per minute")
def register():
    if g.user:return redirect(url_for("dashboard"))
    if request.method=="POST":
        username=request.form.get("username","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        phone=request.form.get("phone","")
        if not username or not email or not password or not phone:
            flash("All fields are required.","error")
            return render_template("register.html")
        
        if len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password) or not any(not c.isalnum() for c in password):
            flash("Password does not meet security requirements.","error")
            return render_template("register.html")

        if User.query.filter_by(username=username).first():
            flash("Username already exists.","error")
            return render_template("register.html")
        if User.query.filter_by(phone=phone).first():
            flash("Phone already registered.","error")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already exists.","error")
            return render_template("register.html")
        
        user=User(username=username,email=email,password_hash=hash_password(password),login_method="email", phone=phone)
        db.session.add(user)
        db.session.commit()
        notify(user.id,"Welcome to Tunu Library","Your account has been created.")
        try:mail.send(email,"Welcome to Tunu Library",f"<h2>Welcome to Tunu Library</h2><p>Hello {username}, your account is ready.</p>")
        except:pass
        flash("Account created successfully.","success")
        return login_response(user)
    return render_template("register.html")

@app.route("/login",methods=["GET","POST"])
@limiter.limit("5 per minute")
def login():
    if g.user:return redirect(url_for("dashboard"))
    if request.method=="POST":
        identifier=request.form.get("identity","").strip()
        password=request.form.get("password","")
        user=User.query.filter(db.or_(User.email==identifier.lower(),User.username==identifier)).first()
        if not user or not user.password_hash or not verify_password(password,user.password_hash):
            flash("Invalid login details.","error")
            return render_template("login.html")
        flash(f"Welcome back, {user.username}!","success")
        return login_response(user)
    return render_template("login.html")

@app.route("/google/login")
def google_login():
    flow = Flow.from_client_secrets_file(
        GOOGLE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=url_for("callback", _external=True)
    )

    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true"
    )

    session["google_state"] = state
    session["google_code_verifier"] = flow.code_verifier

    return redirect(auth_url)

@app.route("/google/callback")
def callback():
    try:
        if "google_state" not in session or "google_code_verifier" not in session:
            flash("Google registration session expired. Please try again.", "error")
            return redirect(url_for("register"))

        flow = Flow.from_client_secrets_file(
            GOOGLE_CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            state=session["google_state"],
            redirect_uri=url_for("callback", _external=True)
        )

        flow.code_verifier = session["google_code_verifier"]
        flow.fetch_token(authorization_response=request.url)

        if not flow.credentials or not flow.credentials.token:
            flash("Google authentication failed. Please try again.", "error")
            return redirect(url_for("register"))

        r = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {flow.credentials.token}"},
            timeout=10
        )
        r.raise_for_status()

        info = r.json()
        google_id = info.get("sub")
        email = info.get("email", "").strip().lower()
        username = info.get("name", "").strip()

        if not google_id or not email or not username:
            flash("Unable to get your Google account information.", "error")
            return redirect(url_for("register"))

        user = User.query.filter_by(google_id=google_id).first()

        if not user:
            user = User.query.filter_by(email=email).first()

        if user:
            if not user.is_active:
                flash("Your account has been suspended. Please contact support.", "error")
                return redirect(url_for("register"))

            if user.google_id and user.google_id != google_id:
                flash("This email is linked to another Google account.", "error")
                return redirect(url_for("register"))

            user.google_id = google_id
            user.login_method = "google"
            user.tkv = gen_id("TK", 10)

        else:
            if User.query.filter_by(username=username).first():
                username = f"{username}{random.randint(1000, 9999)}"

            user = User(
                google_id=google_id,
                username=username,
                email=email,
                login_method="google",
                password_hash=None,
                is_active=True,
                tkv=gen_id("TK", 10)
            )

            db.session.add(user)

        db.session.commit()

        session["user_id"] = user.id
        session["token"] = generate_token(user.id, user.tkv)
        session["user_name"] = user.username

        session.pop("google_state", None)
        session.pop("google_code_verifier", None)

        flash("Logged in Successfully", "success")
        return login_response(user)

    except requests.exceptions.RequestException:
        db.session.rollback()
        flash("Unable to connect to Google. Please try again.", "error")
        return redirect(url_for("register"))

    except Exception:
        db.session.rollback()
        app.logger.exception("Google callback error")
        flash("Google registration failed. Please try again.", "error")
        return redirect(url_for("register"))

@app.route("/admin/login")
def admin_login():
    if g.user and g.user.is_admin:
        return redirect(url_for("admin_dashboard"))
    next_url=request.args.get("next", "/admin")
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url="/admin"
    return redirect(url_for("login", next=next_url))

@app.route("/logout")
def logout():

    flash("You have been logged out.","success")
    r=make_response(redirect(url_for("index")))
    r.delete_cookie("tunu_session",path="/")
    return r

@app.route("/dashboard")
def dashboard():
    x=auth()
    if x:return x
    if not g.user.membership_expires or g.user.membership_expires<datetime.utcnow():
        flash("Your membership has expired, please renew again to be able to borrow a book")
        return redirect(url_for(f'membership'))
    expires = g.user.membership_expires
    borrowings=Borrowing.query.filter_by(user_id=g.user.id).all()
    reservations=Reservation.query.filter_by(user_id=g.user.id).order_by(Reservation.reserved_at.desc()).all()
    return render_template("dashboard.html",borrowings=borrowings,reservations=reservations, expires=expires)

@app.route("/reserve/<book_id>",methods=["POST"])
def reserve_book(book_id):
    x=auth()
    if x:return x
    book=find_book(book_id)
    if not book:abort(404)
    existing=Reservation.query.filter(Reservation.user_id==g.user.id,Reservation.book_id==book_id,Reservation.status.in_(["pending","approved"])).first()
    if existing:return jsonify(error="Already reserved"),400
    r=Reservation(user_id=g.user.id,book_id=book["id"],book_title=book["title"],book_authors=book.get("authors"),book_image=book.get("image"))
    db.session.add(r)
    db.session.commit()
    notify(g.user.id,"Reservation Received",f"Your reservation for {book['title']} was received.")
    for u in User.query.filter_by(is_admin=True).all():
        notify(u.id,"New Reservation",f"{g.user.username} reserved {book['title']}.")
    flash(f"{book['title']} added to your reserve. Pay {BORROWING_FEE_BOB} BOB from dashboard when ready.","success")
    return redirect(url_for("my_reservations"))

@app.route("/borrow/<book_id>",methods=["POST"])
def borrow_book(book_id):
    x=auth()
    if x:return x
    book=find_book(book_id)
    if not book:abort(404)
    existing=Borrowing.query.filter(Borrowing.user_id==g.user.id,Borrowing.book_id==book["id"],Borrowing.status.in_(["requested","pending","borrowed"])).first()
    if existing:
        if existing.status == "borrowed":
            flash("You are already borrowing this book.","warning")
        else:
            flash("You have already requested this book. Check My Borrowings for the pickup date.","warning")
        return redirect(url_for("my_borrowings"))

    if not g.user.membership_expires or g.user.membership_expires<datetime.utcnow():
        flash("Your membership has expired, please renew again to be able to borrow a book")
        return redirect(url_for(f'membership'))
    
    requested_at=datetime.utcnow()
    pickup_at=requested_at+timedelta(days=PICKUP_WAIT_DAYS)
    borrowing=Borrowing(user_id=g.user.id,book_id=book["id"],book_title=book["title"],book_authors=book.get("authors"),book_image=book.get("image"),status="requested",borrowed_at=requested_at,pickup_at=pickup_at,due_date=requested_at+timedelta(days=BORROWING_WINDOW_DAYS))
    db.session.add(borrowing)
    db.session.commit()
    notify(g.user.id,"Borrowing Request Received",f"Your request for {book['title']} was received. Come collect it on {pickup_at.strftime('%d %b %Y')} after admin confirmation.")
    for u in User.query.filter_by(is_admin=True).all():
        notify(u.id,"New Borrowing Request",f"{g.user.username} requested {book['title']}. Pickup is scheduled for {pickup_at.strftime('%d %b %Y')}.")
    flash(f"{book['title']} requested. Come to get it on {pickup_at.strftime('%d %b %Y')} after admin confirmation.","success")
    print('Pay borrowing called')
    return redirect(url_for("pay_borrowing", borrowing_id=borrowing.id))

@app.route("/my-borrowings")
def my_borrowings():
    x=auth()
    if x:return x
    items=Borrowing.query.filter_by(user_id=g.user.id).order_by(Borrowing.borrowed_at.desc()).all()
    return render_template("borrowings.html",borrowings=items,borrowing_fee_bob=BORROWING_FEE_BOB,borrowing_window_days=BORROWING_WINDOW_DAYS,membership_fee_bob=MEMBERSHIP_FEE_BOB,pickup_wait_days=PICKUP_WAIT_DAYS)

@app.route("/service-worker.js")
def service_worker():
    response=make_response(send_from_directory(app.static_folder,"service-worker.js",mimetype="application/javascript"))
    response.headers["Service-Worker-Allowed"]="/"
    response.headers["Cache-Control"]="no-cache"
    return response

@app.route("/terms-of-service")
def terms_of_service():
    return render_template("terms.html")

@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy.html")

@app.route("/borrowing-rules")
def borrowing_rules():
    return render_template(
        "borrowing_rules.html",
        membership_fee_bob=MEMBERSHIP_FEE_BOB,
        borrowing_fee_bob=BORROWING_FEE_BOB,
        borrowing_window_days=BORROWING_WINDOW_DAYS,
        pickup_wait_days=PICKUP_WAIT_DAYS,
    )

@app.route("/help_center")
def help_center():
    return render_template('support.html')

@app.route("/membership", methods=['POST', 'GET'])
def membership():
    x=auth()
    if x:
        return x
    user = g.user
    user.membership_expires = datetime.utcnow() + timedelta(days=365)
    db.session.commit()
    if request.method == 'POST':
        user = g.user
        user.membership_expires = datetime.utcnow() + timedelta(days=365)
        db.session.commit()
        flash('Membership activated successfully', 'success')
        return redirect(url_for('dashboard'))
        
    m_type = 'activate' 
    
    if g.user.membership_expires and g.user.membership_expires < datetime.utcnow():
        m_type='renew'   
        
    return render_template("membership.html",type = m_type, membership_fee=MEMBERSHIP_FEE_BOB)

@app.route("/my-reservations")
def my_reservations():
    x=auth()
    if x:return x
    items=Reservation.query.filter_by(user_id=g.user.id).order_by(Reservation.reserved_at.desc()).all()
    return render_template("reservations.html",reservations=items,borrowing_fee_bob=BORROWING_FEE_BOB,borrowing_window_days=BORROWING_WINDOW_DAYS,membership_fee_bob=MEMBERSHIP_FEE_BOB)

@app.route("/borrowings/<int:borrowing_id>/pay",methods=["GET","POST"])
def pay_borrowing(borrowing_id):
    x=auth()
    if x:return x
    
    admin_id = request.args.get('admin_id')

    borrowing=Borrowing.query.filter_by(
        id=borrowing_id,
        user_id=g.user.id
    ).first_or_404()
    
    admin = User.query.filter_by(id=admin_id).first()
    
    if admin_id and admin.is_admin:
        borrowing.fee_paid = True
        borrowing.status ='paid'
        db.session.commit()


    if borrowing.status == "returned":
        flash("A returned book cannot receive a borrowing payment.","warning")
        if not admin or not admin.is_admin:
           return redirect(url_for("my_borrowings"))
        return redirect(url_for("admin_borrowing"))
    
    if borrowing.fee_paid:
        flash("This borrowing has already been paid.","success")
        if not admin or not admin.is_admin:
           return redirect(url_for("my_borrowings"))
        return redirect(url_for("admin_borrowing"))

    if request.method=="POST":
        phone=getattr(g.user,"phone",None) or request.form.get("phone")
        
        if borrowing.fee_paid:
            flash("This borrowing has already been paid.","success")
            if not admin or not admin.is_admin:
               return redirect(url_for("my_borrowings"))
            return redirect(url_for("admin_borrowing"))
       
        if not phone:
            flash("Add a phone number before starting M-Pesa payment.","warning")
            return render_template(
                "pay_borrowing.html",
                borrowing=borrowing,
                borrowing_fee_bob=BORROWING_FEE_BOB
            )

        if not mpesa_is_configured():
            flash("M-Pesa is not configured yet.","warning")
            return render_template(
                "pay_borrowing.html",
                borrowing=borrowing,
                borrowing_fee_bob=BORROWING_FEE_BOB
            )

        try:
            phone=normalize_phone(phone)

            token=generate_hmac_token({"borrowing_id":borrowing.id})

            callback_url=url_for(
                "mpesa_borrowing_callback",
                _external=True,
                borrowing_id=borrowing.id,
                token=token
            )

            print("CALLBACK URL:",callback_url)

            result=initiate_mpesa_payment(
                BORROWING_FEE_BOB,
                phone,
                callback_url,
                str(borrowing.id)
            )

            if str(result.get("ResponseCode"))!="0":
                flash(
                    result.get("ResponseDescription") or "M-Pesa could not start the payment.",
                    "danger"
                )
            else:
                flash("M-Pesa payment prompt sent to your phone.","success")

        except Exception as exc:
            print("M-PESA ERROR:",exc)
            app.logger.exception("M-Pesa payment initiation failed")
            flash("Payment could not be started. Please try again later.","danger")

    return render_template(
        "pay_borrowing.html",
        borrowing=borrowing,
        borrowing_fee_bob=BORROWING_FEE_BOB
    )

@app.route("/reservations/<int:res_id>/cancel",methods=["POST"])
def cancel_reservation(res_id):
    x=auth()
    if x:return x
    r=Reservation.query.filter_by(id=res_id,user_id=g.user.id).first_or_404()
    if r.status not in ["pending","approved", "wishlist"]:
        flash("This item can no longer be removed.","warning")
        return redirect(url_for("my_reservations"))
    
    db.session.delete(r)
    db.session.commit()
    flash("Item removed from wishlist.","success")
    return redirect(url_for("my_reservations"))

@app.route("/notifications/<int:id>/read",methods=["GET"])
@limiter.limit("40 per minute")
def notification_read(id):
    x=auth()
    if x:return x
    n=Notification.query.filter_by(id=id,user_id=g.user.id).first_or_404()
    n.is_read=True
    db.session.commit()
    return redirect(url_for('notifications_tab'))

@app.route("/admin")
def admin_dashboard():
    admin()
    return render_template("admin/dashboard.html",total_books=LibraryBook.query.count(),pending_reservations=Reservation.query.filter_by(status="pending").count(),active_borrowings=Borrowing.query.filter(Borrowing.status.in_(["requested","borrowed"])).count(),total_users=User.query.count())

@app.route("/admin/reservations")
def admin_reservations():
    admin("reservations")
    page = request.args.get("page", 1, type=int)
    pagination = Reservation.query.order_by(Reservation.reserved_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/reservations.html", reservations=pagination.items, pagination=pagination)

@app.route("/admin/reservations/<int:id>/approve",methods=["POST"])
def approve_reservation(id):
    admin("reservations")
    r=Reservation.query.get_or_404(id)
    if r.status=="approved":
        flash("Reservation already approved.","warning")
        return redirect(url_for("admin_reservations"))
    r.status="approved"
    r.approved_at=datetime.utcnow()
    r.expires_at=datetime.utcnow()+timedelta(days=3)
    db.session.commit()
    notify(r.user_id,"Reservation Approved",f"{r.book_title} is ready for pickup.")
    if r.user.email:
        try:mail.send(r.user.email,"Your Tunu Library Reservation Is Ready",f"<h2>Reservation Approved</h2><p>{r.book_title} is ready for pickup.</p>")
        except:pass
    flash(f"{r.book_title} reservation approved.","success")
    return redirect(url_for("admin_reservations"))

@app.route("/admin/borrowing")
def admin_borrowing():
    admin("borrowing")
    page = request.args.get("page", 1, type=int)
    show_closed = request.args.get("show_closed", "0") == "1"
    
    query = Borrowing.query
    if not show_closed:
        query = query.filter(Borrowing.status != "returned")
        
    pagination = query.order_by(Borrowing.borrowed_at.desc()).paginate(page=page, per_page=20, error_out=False)
    manual_books=(db.session.query(Book.id,Book.title,Book.authors)
        .outerjoin(LibraryBook,LibraryBook.book_id==Book.id)
        .filter(Book.is_deleted==False,or_(LibraryBook.id.is_(None),LibraryBook.available_copies>0))
        .order_by(Book.title).all())
    return render_template("admin/borrowings.html", borrowings=pagination.items, pagination=pagination, show_closed=show_closed, manual_books=manual_books)

@app.route("/admin/borrowing/<int:borrowing_id>/borrowed",methods=["POST"])
def mark_borrowed(borrowing_id):
    admin("borrowing")
    borrowing=Borrowing.query.get_or_404(borrowing_id)
    if borrowing.status == "borrowed":
        flash("This borrowing is already marked as borrowed.","warning")
        return redirect(url_for("admin_borrowing"))

    if not borrowing.status == "paid":
        flash("This borrowing is not yet marked as paid.","warning")
        return redirect(url_for("admin_borrowing"))
    
    if borrowing.status == "returned":
        flash("A returned borrowing cannot be reopened from this action.","warning")
        return redirect(url_for("admin_borrowing"))
    library=LibraryBook.query.filter_by(book_id=borrowing.book_id).first()
    if library and library.available_copies <= 0:
        flash("There are no available copies to confirm for pickup.","warning")
        return redirect(url_for("admin_borrowing"))
    if library:
        library.available_copies=max(0,library.available_copies-1)
    borrowing.status="borrowed"
    borrowing.picked_up_at=datetime.utcnow()
    borrowing.due_date=borrowing.picked_up_at+timedelta(days=BORROWING_WINDOW_DAYS)
    db.session.commit()
    notify(borrowing.user_id,"Book Ready for Reading",f"Your pickup of {borrowing.book_title} was confirmed. Return it by {borrowing.due_date.strftime('%d %b %Y')}.")
    flash(f"{borrowing.book_title} marked as borrowed.","success")
    return redirect(url_for("admin_borrowing"))

@app.route("/admin/borrowing/<int:borrowing_id>/returned",methods=["POST"])
@limiter.limit("40 per minute")
def mark_returned(borrowing_id):
    admin("borrowing")
    borrowing=Borrowing.query.get_or_404(borrowing_id)
    if borrowing.status == "returned":
        flash("This borrowing is already marked as returned.","warning")
        return redirect(url_for("admin_borrowing"))
    library=LibraryBook.query.filter_by(book_id=borrowing.book_id).first()
    if library:
        library.available_copies=min(library.total_copies,library.available_copies+1)
    borrowing.status="returned"
    borrowing.returned_at=datetime.utcnow()
    db.session.commit()
    notify(borrowing.user_id if borrowing.user_id else 1,"Book Returned",f"Your return of {borrowing.book_title} was recorded.")
    flash("Borrowing marked as returned.","success")
    return redirect(url_for("admin_borrowing"))

@app.route("/admin/users")
def admin_users():
    admin("users")
    page = request.args.get("page", 1, type=int)
    pagination = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/users.html", users=pagination.items, pagination=pagination)

@app.route("/admin/payments")
def admin_payments():
    admin("payments")
    page = request.args.get("page", 1, type=int)
    pagination = Payment.query.order_by(Payment.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template("admin/payments.html", payments=pagination.items, pagination=pagination)

@app.route("/admin/notifications")
def admin_notifications():
    admin("notifications")
    return render_template("admin/notifications.html",notifications=Notification.query.order_by(Notification.created_at.desc()).all())

@app.route("/admin/manage-admins")
def manage_admins():
    admin("admins")
    return render_template("admin/admins.html",users=User.query.filter_by(is_admin=True).all())

@app.route("/admin/manage-admins/<int:user_id>",methods=["POST"])
def update_admin_permissions(user_id):
    admin("admins")
    user=User.query.get_or_404(user_id)
    permissions=request.form.getlist("permissions")
    allowed={"users","books","reservations","borrowing","payments","notifications","admins","settings","flags"}
    AdminPermission.query.filter_by(user_id=user.id).delete()
    for permission in permissions:
        if permission in allowed:
            db.session.add(AdminPermission(user_id=user.id,permission=permission,enabled=True))
    user.is_admin=True
    db.session.commit()
    flash(f"Permissions for {user.username} updated.","success")
    return redirect(url_for("manage_admins"))

@app.route("/api/pay", methods=["POST"])
def pay():
    return jsonify({"error": "This endpoint is for the borrowing payment flow. Use /borrowings/<borrowing_id>/pay."}), 410


@app.route("/open/notifications/newtab")
def notifications_tab():
    unread_notifications = unread(g.user.id) if g.user else None
    return render_template('notifications.html', notifications=unread_notifications)

@app.route("/mpesa/borrowing-callback", methods=["POST"])
def mpesa_borrowing_callback():
    token = request.args.get("token")
    borrowing_id = request.args.get("borrowing_id", type=int)
    if not token or not borrowing_id or not verify_hmac_token({"borrowing_id": borrowing_id}, token):
        abort(403, description="Access denied: unverified transaction token.")

    try:
        payload = request.get_json(silent=True) or {}
        callback = payload.get("Body", {}).get("stkCallback", {})
        borrowing = db.session.get(Borrowing, borrowing_id)
        if not borrowing:
            return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})
        
        
        if callback.get("ResultCode") == 0:
            if not borrowing.fee_paid:
                borrowing.fee_paid=True
                borrowing.status="paid"
                metadata=callback.get("CallbackMetadata",{}).get("Item",[])

                receipt=next((item.get("Value") for item in metadata if item.get("Name")=="MpesaReceiptNumber"),None)
                db.session.add(Payment(user_id=borrowing.user_id,amount=BORROWING_FEE_BOB,payment_type="borrowing",status="completed",reference=receipt or f"BORROWING-{borrowing.id}-{int(datetime.utcnow().timestamp())}"))
            db.session.commit()
            notify(borrowing.user_id,"Borrowing Payment Received",f"Payment received for borrowing #{borrowing.id}.")
        else:
            db.session.commit()
    except Exception:
        app.logger.exception("M-Pesa callback processing failed")
        db.session.rollback()
    return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})

@app.route("/api/wishlist/sync", methods=["POST"])
def sync_wishlist():
    if not g.user:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    wishlist = data.get("wishlist", [])
    
    for item in wishlist:
        book_id = item.get("id")
        if not book_id:
            continue
            
        # Check if already in wishlist/reservations
        exists = Reservation.query.filter_by(user_id=g.user.id, book_id=book_id).first()
        if not exists:
            res = Reservation(
                user_id=g.user.id,
                book_id=book_id,
                book_title=item.get("title", "Unknown Title"),
                book_authors=item.get("authors", ""),
                book_image=item.get("image", ""),
                status="wishlist"
            )
            db.session.add(res)
    
    db.session.commit()
    return jsonify({"success": True})

@app.route("/admin/borrowing/manual", methods=["POST"])
def admin_manual_borrowing():
    admin("borrowing")
    book_id = request.form.get("book_id")
    name = request.form.get("name","").strip()
    phone = request.form.get("phone","").strip()
    reference=request.form.get("reference","").strip()
    if not book_id or not name or not phone:
        flash("Book ID, borrower name, and phone number are required.","error")
        return redirect(url_for("admin_borrowing"))
    
    book = find_book(book_id)
    if not book:
        flash("Book not found.", "error")
        return redirect(url_for("admin_borrowing"))
    library=LibraryBook.query.filter_by(book_id=book_id).first()
    if library and library.available_copies<=0:
        flash("There are no available copies for this book.","error")
        return redirect(url_for("admin_borrowing"))
        
    borrowing = Borrowing(
        walkin_name=name,
        walkin_phone=phone,
        book_id=book_id,
        book_title=book["title"],
        book_authors=book.get("authors"),
        book_image=book.get("image"),
        status="borrowed",
        picked_up_at=datetime.utcnow(),
        due_date=datetime.utcnow() + timedelta(days=BORROWING_WINDOW_DAYS),
        fee_paid=True
    )
    db.session.add(borrowing)
    db.session.flush()
    if library:
        library.available_copies=max(0,library.available_copies-1)

    payment = Payment(
        user_id=None,
        amount=BORROWING_FEE_BOB,
        payment_type="manual_borrowing",
        status="completed",
        reference=reference or f"WALKIN-{borrowing.id}-{int(datetime.utcnow().timestamp())}",
        borrowing_id=borrowing.id,
        recorded_by=g.user.id,
    )
    db.session.add(payment)
    db.session.commit()
    
    flash(f"Manual borrowing for {book['title']} saved.", "success")
    return redirect(url_for("admin_borrowing"))

@app.route("/admin/book-flags")
def admin_book_flags():
    admin("flags")
    page=request.args.get("page",1,type=int)
    pagination=BookFlag.query.order_by(BookFlag.created_at.desc()).paginate(page=page,per_page=20,error_out=False)
    return render_template("admin/book_flags.html",flags=pagination.items,pagination=pagination)

@app.route("/admin/book-flags/<int:flag_id>/review",methods=["POST"])
def review_book_flag(flag_id):
    admin("flags")
    flag=BookFlag.query.get_or_404(flag_id)
    status=request.form.get("status","").strip().lower()
    note=request.form.get("admin_note","").strip()
    if status not in {"reviewed","resolved","dismissed"}:
        flash("Choose a valid flag status.","error")
        return redirect(url_for("admin_book_flags"))
    flag.status=status
    flag.admin_note=note or None
    flag.reviewed_by=g.user.id
    flag.reviewed_at=datetime.utcnow()
    flag.resolved_at=datetime.utcnow() if status in {"resolved","dismissed"} else None
    db.session.commit()
    if flag.user_id:
        notify(flag.user_id,"Book flag updated",f"Your flag for book {flag.book_id} is now {status}.")
    flash("Book flag updated.","success")
    return redirect(url_for("admin_book_flags"))

@app.route("/admin/books")
def admin_books():
    admin("books")
    page = request.args.get("page", 1, type=int)
    pagination = LibraryBook.query.paginate(page=page, per_page=20, error_out=False)
    
    books = []
    for lb in pagination.items:
        details = find_book(lb.book_id)
        if details:
            details['available_copies'] = lb.available_copies
            details['total_copies'] = lb.total_copies
            books.append(details)
            
    return render_template("admin/books.html", books=books, pagination=pagination)


def render_http_error(code):
    return render_template(f"{code}.html"),code

@app.errorhandler(400)
def bad_request(error):
    return render_http_error(400)

@app.errorhandler(401)
def unauthorized(error):
    return render_http_error(401)


@app.errorhandler(403)
def forbidden(error):
    return render_http_error(403)

@app.errorhandler(404)
def page_not_found(error):
    return render_http_error(404)

@app.errorhandler(405)
def method_not_allowed(error):
    return render_http_error(405)

@app.errorhandler(408)
def request_timeout(error):
    return render_http_error(408)

@app.errorhandler(409)
def conflict(error):
    return render_http_error(409)

@app.errorhandler(410)
def gone(error):
    return render_http_error(410)

@app.errorhandler(413)
def request_entity_too_large(error):
    return render_http_error(413)

@app.errorhandler(415)
def unsupported_media_type(error):
    return render_http_error(415)

@app.errorhandler(422)
def unprocessable_content(error):
    return render_http_error(422)

@app.errorhandler(429)
def too_many_requests(error):
    return render_http_error(429)

@app.errorhandler(500)
def internal_server_error(error):
    db.session.rollback()
    return render_http_error(500)

@app.errorhandler(502)
def bad_gateway(error):
    return render_http_error(502)

@app.errorhandler(503)
def service_unavailable(error):
    return render_http_error(503)

@app.errorhandler(504)
def gateway_timeout(error):
    return render_http_error(504)

@app.cli.command("init-db")
def init_db():
    with app.app_context():db.create_all()
    print("Database created.")

if __name__=="__main__":
    with app.app_context():db.create_all()
    app.run(debug=True, port=5000, host='0.0.0.0')
    

