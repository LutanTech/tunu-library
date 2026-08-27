from models import db,Notification

def notify(user_id,title,message):
    n=Notification(user_id=user_id,title=title,message=message)
    db.session.add(n)
    db.session.commit()
    return n

def read(notification_id,user_id):
    n=Notification.query.filter_by(
        id=notification_id,user_id=user_id
    ).first()
    if not n:return False
    n.is_read=True
    db.session.commit()
    return True

def unread(user_id):
    return Notification.query.filter_by(
        user_id=user_id,is_read=False
    ).all()

def unreadCount(user_id):
    return Notification.query.filter_by(
        user_id=user_id,is_read=False
    ).count()