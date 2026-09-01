import os,hmac,hashlib,base64,json,time,requests
from datetime import datetime,timedelta
from sqlalchemy import or_
from werkzeug.security import generate_password_hash,check_password_hash
from models import AdminPermission,LibraryBook,Book,db

import secrets,string, time, random

SECRET=os.getenv("SESSION_SECRET","change-me").encode()
SESSION_DURATION=30*24*60*60
BOOKS_API=os.getenv("BOOKS_API","https://tunupublishers.com/api/books")
BOOKS_SITE=os.getenv("BOOKS_SITE","https://tunupublishers.com")
DELTA_HOURS=3
secret_key = os.getenv("MAIL_PASS")

def hash_password(password):
    return generate_password_hash(password)

def verify_password(password,password_hash):
    return check_password_hash(password_hash,password)

def create_session(user_id):
    now=int(time.time())
    payload={"user_id":user_id,"created_at":now,"expires_at":now+SESSION_DURATION}
    data=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
    sig=hmac.new(SECRET,data,hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(data).decode()}.{base64.urlsafe_b64encode(sig).decode()}"

def gen_id(prefix="ID", length=10, upper=False):
    letters = string.ascii_letters
    if upper:
        letters = letters.upper()
    chars = letters + string.digits
    return prefix + "-" + "".join(secrets.choice(chars) for _ in range(length))


def generate_token(user_id, tkv, expires_in=86400):
    payload = {
        "user_id": user_id,
        "tkv": tkv,
        "exp": int(time.time()) + expires_in
    }

    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode()

    signature = hmac.new(
        secret_key.encode(),
        payload_b64.encode(),
        hashlib.sha256
    ).hexdigest()

    return f"{payload_b64}::{signature}"



def generate_otp():
    return str(random.randint(100000, 999999))



def verify_session(token):
    try:
        a,b=token.split(".",1)
        data=base64.urlsafe_b64decode(a)
        sig=base64.urlsafe_b64decode(b)
        expected=hmac.new(SECRET,data,hashlib.sha256).digest()
        if not hmac.compare_digest(sig,expected): return None
        payload=json.loads(data)
        return payload if payload.get("expires_at",0)>int(time.time()) else None
    except Exception:
        return None

def generate_hmac_token(payload):
    data=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
    sig=hmac.new(SECRET,data,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")

def verify_hmac_token(payload,token):
    if not token:return False
    try:
        data=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
        expected=hmac.new(SECRET,data,hashlib.sha256).digest()
        supplied=base64.urlsafe_b64decode(token+"="*((4-len(token)%4)%4))
        return hmac.compare_digest(supplied,expected)
    except Exception:
        return False

def get_book_image(book):
    image=book.get("image") if isinstance(book,dict) else getattr(book,"image",None)
    if not image:return None
    return image if str(image).startswith(("http://","https://")) else f"{BOOKS_SITE.rstrip('/')}/{str(image).lstrip('/')}"

def book_to_dict(book):
    if not book:return None
    if isinstance(book,dict):
        result=dict(book)
    else:
        result={
            "id":book.id,
            "title":book.title,
            "image":book.image,
            "slug":book.slug,
            "audience":book.audience,
            "grade":book.grade,
            "authors":book.authors,
            "blurb":book.blurb,
            "discounted":book.discounted,
            "oldPrice":book.oldPrice,
            "newPrice":book.newPrice,
            "stars":book.stars,
            "sold":book.sold,
            "views":book.views,
            "is_deleted":book.is_deleted,
            "available_copies":getattr(book,"available_copies",79)
        }
    result["cover"]=get_book_image(book)
    return result

BOOK_FIELDS=("title","image","slug","audience","grade","authors","blurb","discounted","oldPrice","newPrice","stars","sold","views")

def save_book(data,commit=True):
    if not isinstance(data,dict) or not data.get("id"):return None
    book=Book.query.filter_by(id=data["id"]).first()
    if not book:
        book=Book(id=data["id"],added_at=datetime.utcnow()+timedelta(hours=DELTA_HOURS))
        db.session.add(book)
    for field in BOOK_FIELDS:
        if field in data:
            setattr(book,field,data[field])
    book.is_deleted=False
    if commit:
        db.session.commit()
    return book

def _api_books_page(page=1,search="",grade="",status=""):
    params={"page":page,"search":search}
    if grade:params["grade"]=grade
    if status:params["status"]=status
    response=requests.get(BOOKS_API,params=params,timeout=10)
    response.raise_for_status()
    return response.json()

def _sync_catalog(first_page):
    """Store every API page, then soft-hide locally cached books no longer in the API."""
    data=first_page
    page=1
    remote_ids=set()
    while True:
        for item in data.get("books",[]):
            if isinstance(item,dict) and item.get("id"):
                remote_ids.add(item["id"])
                save_book(item,commit=False)
        if not data.get("pagination",{}).get("has_next"):
            break
        page+=1
        data=_api_books_page(page)
    if remote_ids:
        Book.query.filter(~Book.id.in_(remote_ids)).update({"is_deleted":True},synchronize_session=False)
    else:
        Book.query.update({"is_deleted":True},synchronize_session=False)
    db.session.commit()

def get_books(page=1,search="",grade="",status=""):
    page=max(int(page or 1),1)
    per_page=12
    try:
        api_catalog=_api_books_page()
        api_total=int(api_catalog.get("pagination",{}).get("total",0))
        local_total=Book.query.filter_by(is_deleted=False).count()
        if api_total!=local_total:
            _sync_catalog(api_catalog)
    except requests.RequestException as e:
        print(f"[BOOK API COUNT CHECK ERROR] {e}")
    except Exception as e:
        db.session.rollback()
        print(f"[BOOK SYNC ERROR] {e}")

    query=Book.query.filter_by(is_deleted=False)
    if search:
        term=f"%{search.strip()}%"
        query=query.filter(or_(Book.title.ilike(term),Book.authors.ilike(term)))
    if grade:
        query=query.filter(Book.grade==grade)
    if status:
        query=query.outerjoin(LibraryBook,LibraryBook.book_id==Book.id)
        if status=="available":
            query=query.filter(or_(LibraryBook.id.is_(None),LibraryBook.available_copies>0))
        elif status=="reserved":
            query=query.filter(LibraryBook.available_copies<=0)

    total=query.count()
    pages=max((total+per_page-1)//per_page,1)
    items=query.order_by(Book.added_at.desc()).offset((page-1)*per_page).limit(per_page).all()
    books=[]
    for book in items:
        item=book_to_dict(book)
        library_book=LibraryBook.query.filter_by(book_id=book.id).first()
        item["available_copies"]=library_book.available_copies if library_book else 58
        books.append(item)

    labels=[value for (value,) in db.session.query(Book.grade).filter(Book.is_deleted==False,Book.grade.isnot(None)).distinct().order_by(Book.grade).all()]
    return {"books":books,"labels":labels,"pagination":{"page":page,"pages":pages,"has_prev":page>1,"prev_num":page-1 if page>1 else None,"has_next":page<pages,"next_num":page+1 if page<pages else None,"total":total,"per_page":per_page}}

def find_book(book_id):
    if not book_id:return None

    book=Book.query.filter_by(id=book_id,is_deleted=False).first()

    if book:
        return book_to_dict(book)

    for page in range(1,21):
        data=get_books(page)

        for item in data.get("books",[]):
            if isinstance(item,dict) and str(item.get("id"))==str(book_id):
                book=save_book(item)
                if book:
                    result=book_to_dict(book)
                    result["available_copies"]=item.get("available_copies") or item.get("copies") or 58
                    return result
                return None

        if not data.get("pagination",{}).get("has_next"):break

    return None

def has_permission(user,permission):
    if not user or not user.is_admin:return False
    if user.is_super_admin:return True
    return AdminPermission.query.filter_by(user_id=user.id,permission=permission,enabled=True).first() is not None
