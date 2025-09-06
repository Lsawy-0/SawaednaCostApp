from app.extensions import db
from sqlalchemy import event, inspect

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text)

    # --- START: الحقول الجديدة ---
    # الرقم التسلسلي التلقائي للدفعة داخل المستخلص الواحد
    payment_number = db.Column(db.Integer, nullable=True) 
    # الرقم المرجعي اليدوي (رقم سند، حوالة، الخ)
    reference_number = db.Column(db.String(100), nullable=True) 
    # --- END: الحقول الجديدة ---

    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    
    # Relationships
    invoice = db.relationship('Invoice', back_populates='payments')
    distributions = db.relationship('PaymentDistribution', back_populates='payment', cascade="all, delete-orphan")
    
    __table_args__ = (
        db.Index('idx_payment_invoice_id', 'invoice_id'),
    )

    def __repr__(self):
        return f'<Payment {self.amount}>'

# --- START: THE FINAL FIX ---
@event.listens_for(Payment, 'after_insert')
@event.listens_for(Payment, 'after_update')
@event.listens_for(Payment, 'after_delete')
def receive_after_payment_change(mapper, connection, target):
    """
    Updates the parent invoice's status after a payment is changed.
    This function now checks if the session is already working to prevent flushing errors.
    """
    if not target.invoice:
        return

    # Get the session from the target object
    session = inspect(target).session

    # Check if the session is already in the process of flushing using the correct attribute
    if not session._flushing:
        target.invoice.update_status()
        session.flush()
# --- END: THE FINAL FIX ---