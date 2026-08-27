import os, re, base64, requests, traceback
from datetime import datetime,timedelta
from flask import Flask,render_template,request,redirect,url_for,abort,g,make_response,jsonify,flash,send_from_directory
from models import db,User,LibraryBook,Reservation,Borrowing,Payment,Notification,AdminPermission
from utils import *
from emails import mail
from notifications import notify,unread, unreadCount

app=Flask(__name__)
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
    MAIL_USE_TLS=True,
    MAIL_USE_SSL=True,
    MAIL_SERVER="mail.tunupublishers.com",
    MAIL_USERNAME="Tunu Library",
    MAIL_PASSWORD=os.getenv("MAIL_PASS"),
    MAIL_DEFAULT_SENDER="library@tunupublishers.com"
)

CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")
SHORTCODE = os.getenv("MPESA_SHORTCODE")
PASSKEY = os.getenv("MPESA_PASSKEY")
BASE_URL = "https://sandbox.safaricom.co.ke" if os.getenv("MPESA_ENV", "sandbox") == "sandbox" else "https://api.safaricom.co.ke"
MEMBERSHIP_FEE_BOB = 100
BORROWING_FEE_BOB = 100
BORROWING_WINDOW_DAYS = 90

os.environ["OAUTHLIB_INSECURE_TRANSPORT"]="1"
db.init_app(app)
mail.init_app(app)

@app.before_request
def load_user():
    g.user=None
    p=verify_session(request.cookies.get("tunu_session"))
    if p:g.user=db.session.get(User,p["user_id"])

@app.context_processor
def globals():
    return {"current_user":g.user,"unread_notifications":unread(g.user.id) if g.user else None, 
      "unread_count":unreadCount(g.user.id) if g.user else 0
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
        "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        json=payload,
        headers=headers,
        timeout=30
    )

    print("M-PESA STATUS:",response.status_code)
    print("M-PESA RESPONSE:",response.text)
    print("M-PESA PAYLOAD:",payload)

    response.raise_for_status()

    return response.json()

def admin(permission=None):
    if not g.user or not g.user.is_admin:abort(403)
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
    is_already_reserved=False
    if g.user:
        is_already_borrowed=Borrowing.query.filter_by(user_id=g.user.id,book_id=book_id,status="borrowed").first() is not None
        is_already_reserved=Reservation.query.filter(Reservation.user_id==g.user.id,Reservation.book_id==book_id,Reservation.status.in_(["pending","approved"])).first() is not None
        is_member = g.user.membership_expires and g.user.membership_expires > datetime.utcnow() 
    return render_template("book_detail.html",book=item,library=LibraryBook.query.filter_by(book_id=book_id).first(),is_already_borrowed=is_already_borrowed,is_already_reserved=is_already_reserved,membership_fee_bob=MEMBERSHIP_FEE_BOB,borrowing_fee_bob=BORROWING_FEE_BOB,borrowing_window_days=BORROWING_WINDOW_DAYS, is_member=is_member)

@app.route("/register",methods=["GET","POST"])
def register():
    if g.user:return redirect(url_for("dashboard"))
    if request.method=="POST":
        username=request.form.get("username","").strip()
        email=request.form.get("email","").strip().lower()
        password=request.form.get("password","")
        if not username or not email or not password:
            flash("All fields are required.","error")
            return render_template("register.html")
        if User.query.filter_by(username=username).first():
            flash("Username already exists.","error")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("Email already exists.","error")
            return render_template("register.html")
        user=User(username=username,email=email,password_hash=hash_password(password),login_method="email")
        db.session.add(user)
        db.session.commit()
        notify(user.id,"Welcome to Tunu Library","Your account has been created.")
        try:mail.send(email,"Welcome to Tunu Library",f"<h2>Welcome to Tunu Library</h2><p>Hello {username}, your account is ready.</p>")
        except:pass
        flash("Account created successfully.","success")
        return login_response(user)
    return render_template("register.html")

@app.route("/login",methods=["GET","POST"])
def login():
    if g.user:return redirect(url_for("dashboard"))
    if request.method=="POST":
        identifier=request.form.get("identity","").strip()
        password=request.form.get("password","")
        user=User.query.filter(db.or_(User.email==identifier.lower(),User.username==identifier)).first()
        print(identifier, password)
        if not user or not user.password_hash or not verify_password(password,user.password_hash):
            flash("Invalid login details.","error")
            return render_template("login.html")
        flash(f"Welcome back, {user.username}!","success")
        return login_response(user)
    return render_template("login.html")

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
    existing=Borrowing.query.filter_by(user_id=g.user.id,book_id=book["id"],status="pending").first()
    if existing:
        flash("You are already borrowing this book.","warning")
        return redirect(url_for("pay_borrowing",borrowing_id=existing.id))

    if not g.user.membership_expires or g.user.membership_expires<datetime.utcnow():
        flash("Your membership has expired, please renew again to be able to borrow a book")
        return redirect(url_for(f'membership'))
    
    borrowed_at=datetime.utcnow()
    due_date=borrowed_at+timedelta(days=BORROWING_WINDOW_DAYS)
    borrowing=Borrowing(user_id=g.user.id,book_id=book["id"],book_title=book["title"],book_authors=book.get("authors"),book_image=book.get("image"),status="pending", borrowed_at=borrowed_at,due_date=due_date)
    db.session.add(borrowing)
    db.session.commit()
    notify(g.user.id,"Book Borrowed",f"Your borrowing of {book['title']} was recorded.")
    flash(f"{book['title']} added to your borrowings. Pay {BORROWING_FEE_BOB} BOB from My Borrowings when ready.","success")
    return redirect(url_for("pay_borrowing",borrowing_id=borrowing.id))

@app.route("/my-borrowings")
def my_borrowings():
    x=auth()
    if x:return x
    items=Borrowing.query.filter_by(user_id=g.user.id).order_by(Borrowing.borrowed_at.desc()).all()
    return render_template("borrowings.html",borrowings=items,borrowing_fee_bob=BORROWING_FEE_BOB,borrowing_window_days=BORROWING_WINDOW_DAYS,membership_fee_bob=MEMBERSHIP_FEE_BOB)

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
    )

@app.route("/help_center")
def help_center():
    return render_template('support.html')




@app.route("/membership", methods=['POST', 'GET'])
def membership():
    x=auth()
    if x:
        return x
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

    borrowing=Borrowing.query.filter_by(
        id=borrowing_id,
        user_id=g.user.id
    ).first_or_404()

    if borrowing.fee_paid:
        flash("This borrowing has already been paid.","success")
        return redirect(url_for("my_borrowings"))

    if request.method=="POST":
        phone=getattr(g.user,"phone",None) or request.form.get("phone")
        
        if borrowing.fee_paid:
           flash("This borrowing has already been paid.","success")
           return redirect(url_for("my_borrowings"))
       
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
                reseration=borrowing,
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
@app.route("/my-reservations")
def reservations():
    x=auth()
    if x:return x
    items=Reservation.query.filter_by(user_id=g.user.id).order_by(Reservation.reserved_at.desc()).all()
    return render_template("reserves.html",reservations=items)

@app.route("/reservations/<int:res_id>/cancel",methods=["POST"])
def cancel_reservation(res_id):
    x=auth()
    if x:return x
    r=Reservation.query.filter_by(id=res_id,user_id=g.user.id).first_or_404()
    if r.status not in ["pending","approved"]:
        flash("This reservation can no longer be cancelled.","warning")
        return redirect(url_for("dashboard"))
    r.status="cancelled"
    db.session.commit()
    flash("Reservation cancelled successfully.","success")
    return redirect(url_for("dashboard"))

@app.route("/notifications/<int:id>/read",methods=["GET"])
def notification_read(id):
    x=auth()
    if x:return x
    n=Notification.query.filter_by(id=id,user_id=g.user.id).first_or_404()
    n.is_read=True
    db.session.commit()
    return redirect(url_for('index'))

@app.route("/admin")
def admin_dashboard():
    admin()
    return render_template("admin/dashboard.html",users=User.query.count(),reservations=Reservation.query.filter_by(status="pending").count(),borrowings=Borrowing.query.filter_by(status="borrowed").count(),payments=Payment.query.filter_by(status="completed").count())

@app.route("/admin/reservations")
def admin_reservations():
    admin("reservations")
    return render_template("admin/reservations.html",reservations=Reservation.query.order_by(Reservation.reserved_at.desc()).all())

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
    return render_template("admin/borrowings.html",borrowings=Borrowing.query.order_by(Borrowing.borrowed_at.desc()).all())

@app.route("/admin/borrowing/<int:borrowing_id>/returned",methods=["POST"])
def mark_returned(borrowing_id):
    admin("borrowing")
    borrowing=Borrowing.query.get_or_404(borrowing_id)
    borrowing.status="returned"
    if hasattr(borrowing,"returned_at"):
        borrowing.returned_at=datetime.utcnow()
    db.session.commit()
    flash("Borrowing marked as returned.","success")
    return redirect(url_for("admin_borrowing"))

@app.route("/admin/users")
def admin_users():
    admin("users")
    return render_template("admin/users.html",users=User.query.order_by(User.created_at.desc()).all())

@app.route("/admin/payments")
def admin_payments():
    admin("payments")
    return render_template("admin/payments.html",payments=Payment.query.order_by(Payment.created_at.desc()).all())

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
    allowed={"users","books","reservations","borrowing","payments","notifications","admins","settings"}
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
                borrowing.borrowed_at=datetime.utcnow()
                borrowing.due_date=datetime.utcnow() + timedelta(days=90)
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