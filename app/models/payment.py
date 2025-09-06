# app/models/payment.py
from datetime import date
from sqlalchemy import event
from sqlalchemy.sql import text
from app import db

class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    # لازم تكون مرتبطة بجدول الفواتير (invoices)
    invoice_id = db.Column(
        db.Integer,
        db.ForeignKey("invoices.id"),
        nullable=False,
        index=True
    )

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    method = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    # الحقول الجديدة:
    # رقم الدفعة داخل نفس الفاتورة (1، 2، 3، ...)، Unique per (invoice_id, payment_number)
    payment_number = db.Column(db.Integer, nullable=False)

    # رقم مرجعي خارجي (اختياري) — نعمله فهرس لسهولة البحث
    reference_number = db.Column(db.String(64), nullable=True, index=True)

    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        onupdate=db.func.now(),
        nullable=False
    )

    # ضمان عدم تكرار رقم الدفعة لنفس الفاتورة
    __table_args__ = (
        db.UniqueConstraint("invoice_id", "payment_number", name="uq_invoice_payment_no_per_invoice"),
    )

    # لو عندك علاقة راجعة في موديل الفاتورة (Invoice) خليها هناك:
    # payments = db.relationship("Payment", back_populates="invoice")
    # وهنا تقدر تعمل:
    # invoice = db.relationship("Invoice", back_populates="payments")


@event.listens_for(Payment, "before_insert")
def _assign_incremental_payment_number(mapper, connection, target: "Payment"):
    """
    لو payment_number مش متحدد، نعيّنه تلقائيًا = (أقصى رقم لنفس الفاتورة + 1).
    ده يمنع التعارض، ومع الـUniqueConstraint يضمنلك ترتيب نظيف.
    """
    if target.payment_number is None:
        # بنستخدم SQL مباشر لضمان الشغل على نفس الاتصال (connection)
        max_sql = text("""
            SELECT MAX(payment_number) AS max_no
            FROM payments
            WHERE invoice_id = :inv_id
        """)
        res = connection.execute(max_sql, {"inv_id": target.invoice_id}).scalar()
        target.payment_number = 1 if res is None else int(res) + 1
