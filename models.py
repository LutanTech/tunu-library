from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy

db=SQLAlchemy()

class User(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    username=db.Column(db.String(80),unique=True,nullable=True)
    email=db.Column(db.String(150),unique=True,nullable=True)
    password_hash=db.Column(db.String(255),nullable=True)
    google_id=db.Column(db.String(255),unique=True,nullable=True)
    login_method=db.Column(db.String(20),default="email",nullable=False)
    is_admin=db.Column(db.Boolean,default=False)
    is_super_admin=db.Column(db.Boolean,default=False)
    membership_expires=db.Column(db.DateTime)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)

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
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    book_id=db.Column(db.String(80),nullable=False)
    book_title=db.Column(db.String(255),nullable=False)
    book_authors=db.Column(db.String(500))
    book_image=db.Column(db.String(500))
    borrowed_at=db.Column(db.DateTime,default=datetime.utcnow)
    due_date=db.Column(db.DateTime,nullable=False)
    returned_at=db.Column(db.DateTime)
    status=db.Column(db.String(30),default="borrowed")
    fee_paid=db.Column(db.Boolean,default=False)
    rated=db.Column(db.Boolean,default=False)
    rating=db.Column(db.Integer)
    user=db.relationship("User",backref="borrowings")
    

class Payment(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    amount=db.Column(db.Integer,nullable=False)
    payment_type=db.Column(db.String(30),nullable=False)
    status=db.Column(db.String(30),default="pending")
    reference=db.Column(db.String(150),unique=True)
    created_at=db.Column(db.DateTime,default=datetime.utcnow)
    user=db.relationship("User",backref="payments")

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