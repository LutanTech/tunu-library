from flask import render_template
from models import db, User, Notification
from emails import mail


def notify(user_id, title, message):
    n = Notification(
        user_id=user_id,
        title=title,
        message=message
    )

    db.session.add(n)
    db.session.commit()

    user = db.session.get(User, user_id)

    if user and user.email:
        try:
            mail.send(
                user.email,
                title,
                render_template(
                    "emails/notification.html",
                    user=user,
                    title=title,
                    message=message
                )
            )
        except Exception:
            import logging
            logging.exception(
                "Failed to send notification email to %s",
                user.email
            )

    return n


def read(notification_id, user_id):
    n = Notification.query.filter_by(
        id=notification_id,
        user_id=user_id
    ).first()

    if not n:
        return False

    n.is_read = True
    db.session.commit()
    return True


def unread(user_id):
    return Notification.query.filter_by(
        user_id=user_id,
        is_read=False
    ).order_by(
        Notification.created_at.desc()
    ).all()


def unreadCount(user_id):
    return Notification.query.filter_by(
        user_id=user_id,
        is_read=False
    ).count()