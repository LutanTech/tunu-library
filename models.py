from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
import secrets,string, time, random
import os,hmac,hashlib,base64,json,time,requests

db=SQLAlchemy()


def gen_id(prefix="ID", length=10, upper=False):
    letters = string.ascii_letters
    if upper:
        letters = letters.upper()
    chars = letters + string.digits
    return prefix + "-" + "".join(secrets.choice(chars) for _ in range(length))

class User(db.Model):
    id=db.Column(db.String(50),primary_key=True, default=lambda: gen_id("USER", 10))
    google_id=db.Column(db.String(), nullable=True)
    
    username=db.Column(db.String(80),unique=True,nullable=True)
    email=db.Column(db.String(150),unique=True,nullable=True)
    phone=db.Column(db.String(150),nullable=True)
    password_hash=db.Column(db.String(255),nullable=True)
    google_id=db.Column(db.String(255),unique=True,nullable=True)
    login_method=db.Column(db.String(20),default="email",nullable=False)
    is_admin=db.Column(db.Boolean,default=False)
    is_super_admin=db.Column(db.Boolean,default=False)
    is_active=db.Column(db.Boolean,default=False)
    
    membership_expires=db.Column(db.DateTime)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    tkv = db.Column(db.String(50), unique=True, index=True, default=lambda: gen_id("TK", 10))

class LibraryBook(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    book_id=db.Column(db.String(80),unique=True,nullable=False)
    total_copies=db.Column(db.Integer,default=1)
    available_copies=db.Column(db.Integer,default=1)

class Reservation(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    book_id=db.Column(db.String(80),nullable=False)
    book_title=db.Column(db.String(255),nullable=False)
    book_authors=db.Column(db.String(500))
    book_image=db.Column(db.String(500))
    status=db.Column(db.String(30),default="pending")
    reserved_at=db.Column(db.DateTime,default=datetime.utcnow)
    approved_at=db.Column(db.DateTime)
    expires_at=db.Column(db.DateTime)
    user=db.relationship("User",backref="reservations")
    fee_paid=db.Column(db.Boolean,default=False)

class Borrowing(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=True)
    walkin_name=db.Column(db.String(150))
    walkin_phone=db.Column(db.String(20))
    book_id=db.Column(db.String(80),nullable=False)
    book_title=db.Column(db.String(255),nullable=False)
    book_authors=db.Column(db.String(500))
    book_image=db.Column(db.String(500))
    borrowed_at=db.Column(db.DateTime,default=datetime.utcnow)
    pickup_at=db.Column(db.DateTime)
    picked_up_at=db.Column(db.DateTime)
    due_date=db.Column(db.DateTime,nullable=True)
    returned_at=db.Column(db.DateTime)
    status=db.Column(db.String(30),default="requested")

    fee_paid=db.Column(db.Boolean,default=False)
    rated=db.Column(db.Boolean,default=False)
    rating=db.Column(db.Integer)
    user=db.relationship("User",backref="borrowings")
    

class Payment(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=True)
    amount=db.Column(db.Integer,nullable=False)
    payment_type=db.Column(db.String(30),nullable=False)
    status=db.Column(db.String(30),default="pending")
    reference=db.Column(db.String(150),unique=True)
    borrowing_id=db.Column(db.Integer,db.ForeignKey("borrowing.id"),nullable=True)
    recorded_by=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    user=db.relationship("User",foreign_keys=[user_id],backref="payments")
    borrowing=db.relationship("Borrowing",backref="payments")
    recorded_by_user=db.relationship("User",foreign_keys=[recorded_by])

class Notification(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    title=db.Column(db.String(255),nullable=False)
    message=db.Column(db.Text,nullable=False)
    is_read=db.Column(db.Boolean,default=False)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    user=db.relationship("User",backref="notifications")

class AdminPermission(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    permission=db.Column(db.String(80),nullable=False)
    enabled=db.Column(db.Boolean,default=True)
    __table_args__=(db.UniqueConstraint("user_id","permission"),)
    
    
class Order(db.Model):
    id = db.Column(db.Integer,primary_key=True)

    temp_id = db.Column(db.String(255))
    data = db.Column(db.JSON)

    name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(20))

    county = db.Column(db.String(100))
    city = db.Column(db.String(255))
    address = db.Column(db.String(500))

    subtotal = db.Column(db.Float, default=0)
    discount = db.Column(db.Float, default=0)
    delivery_fee = db.Column(db.Float, default=0)
    grand_total = db.Column(db.Float, default=0)

    coupon_code = db.Column(db.String(50))

    checkout_request_id = db.Column(db.String(120))
    mpesa_receipt = db.Column(db.String(50))

    status = db.Column(
        db.String(40),
        default="PENDING"
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.utcnow() + timedelta(hours=3)
    )
    
class Flagged(db.Model):
    id = db.Column(db.Integer,primary_key=True)
    flag_reason = db.Column(db.String(500))
    resolved=db.Column(db.Boolean,default=False)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    borrowing_id=db.Column(db.Integer,db.ForeignKey("borrowing.id"),nullable=False)
    resolved_by=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    resolve_reason = db.Column(db.String(500))
    created_at = db.Column(db.DateTime,default=lambda: datetime.utcnow() + timedelta(hours=3))
    resolved_at = db.Column(db.DateTime,default=lambda: datetime.utcnow() + timedelta(hours=3))
    status = db.Column(db.String(40),default="low")
    


class Book(db.Model):
    id = db.Column(db.String(20), primary_key=True)
    title = db.Column(db.String(512), nullable=False)
    image = db.Column(db.String(1024), default="https://i.ibb.co/CKRYPD4p/image.png")
    slug = db.Column(db.String(120))
    audience, grade = db.Column(db.String(120)), db.Column(db.String(120))
    authors, blurb = db.Column(db.Text), db.Column(db.Text)
    added_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(hours=3))
    edited_at = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(hours=3))
    discounted = db.Column(db.Boolean, default=False)
    oldPrice, newPrice = db.Column(db.Float, default=0), db.Column(db.Float, default=0)
    stars, sold, views = db.Column(db.Integer, default=0), db.Column(db.Integer, default=0), db.Column(db.Integer, default=0)
    is_deleted = db.Column(db.Boolean, default=False)
    

class BookFlag(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    book_id=db.Column(db.String(80),nullable=False)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=True)
    reason=db.Column(db.String(500),nullable=False)
    category=db.Column(db.String(50),default="other")
    details=db.Column(db.Text)
    priority=db.Column(db.String(20),default="normal")
    status=db.Column(db.String(30),default="pending") # pending, reviewed, resolved, dismissed
    admin_note=db.Column(db.Text)
    reviewed_by=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    reviewed_at=db.Column(db.DateTime)
    resolved_at=db.Column(db.DateTime)
    user=db.relationship("User",foreign_keys=[user_id],backref="book_flags")
    reviewed_by_user=db.relationship("User",foreign_keys=[reviewed_by])
