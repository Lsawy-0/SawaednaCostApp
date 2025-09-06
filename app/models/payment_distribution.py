from app.extensions import db

class PaymentDistribution(db.Model):
    """
    Represents the distribution of a single payment amount
    across one or more invoice items.
    """
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)

    # Foreign Keys
    payment_id = db.Column(db.Integer, db.ForeignKey('payments.id'), nullable=False, index=True)
    invoice_item_id = db.Column(db.Integer, db.ForeignKey('invoice_item.id'), nullable=False, index=True)

    # Relationships
    payment = db.relationship('Payment', back_populates='distributions')
    invoice_item = db.relationship('InvoiceItem', back_populates='distributions')

    def __repr__(self):
        return f'<PaymentDistribution {self.amount} for Payment {self.payment_id}>'
