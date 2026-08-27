import os,hmac,hashlib,base64,json,time,requests
from werkzeug.security import generate_password_hash,check_password_hash
from models import AdminPermission

SECRET=os.getenv("SESSION_SECRET","change-me").encode()
SESSION_DURATION=30*24*60*60
BOOKS_API="https://tunupublishers.com/api/books"
BOOKS_SITE="https://tunupublishers.com"


def hash_password(password):
    return generate_password_hash(password)

def verify_password(password,password_hash):
    return check_password_hash(password_hash,password)

def create_session(user_id):
    now=int(time.time())
    payload={"user_id":user_id,"created_at":now,"expires_at":now+SESSION_DURATION}
    data=json.dumps(payload,separators=(",",":")).encode()
    sig=hmac.new(SECRET,data,hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(data).decode()}.{base64.urlsafe_b64encode(sig).decode()}"

def verify_session(token):
    try:
        a,b=token.split(".",1)
        data=base64.urlsafe_b64decode(a)
        sig=base64.urlsafe_b64decode(b)
        expected=hmac.new(SECRET,data,hashlib.sha256).digest()
        if not hmac.compare_digest(sig,expected):
            return None
        payload=json.loads(data)
        return payload if payload["expires_at"]>int(time.time()) else None
    except Exception:
        return None

def generate_hmac_token(payload):
    data=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
    signature=hmac.new(SECRET,data,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).decode().rstrip("=")


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
    image=book.get("image")
    if not image:return None
    return image if image.startswith("http") else f"{BOOKS_SITE}{image}"
def get_books(page=1,search="",grade="",status=""):
    try:
        params={"page":page,"search":search}
        if grade: params["grade"]=grade
        if status: params["status"]=status
        r=requests.get(BOOKS_API,params=params,timeout=10)
        r.raise_for_status()
        data=r.json()

        for book in data.get("books",[]):
            book["cover"]=get_book_image(book)
            book["available_copies"]=book.get("available_copies",book.get("copies", 58)) or 58

        return data
    except requests.RequestException:
        return {"books":[],"labels":[],"pagination":{}}

def find_book(book_id):
    page=1

    while page<=20:
        data=get_books(page)

        for book in data.get("books",[]):
            if book.get("id")==book_id:
                book["available_copies"]=book.get("available_copies", 58) or 58
                return book

        if not data.get("pagination",{}).get("has_next"):
            break

        page+=1

    return None

def has_permission(user,permission):
    if not user or not user.is_admin:return False
    if user.is_super_admin:return True
    return AdminPermission.query.filter_by(
        user_id=user.id,permission=permission,enabled=True
    ).first() is not None