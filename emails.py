from flask_mail import Mail as FlaskMail,Message

class EmailService:
    def __init__(self):
        self._mail=FlaskMail()

    def init_app(self,app):
        self._mail.init_app(app)

    def send(self,to,subject,html):
        self._mail.send(Message(
            subject=subject,
            recipients=[to],
            html=html
        ))

mail=EmailService()