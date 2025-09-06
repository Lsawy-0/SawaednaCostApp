from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from sqlalchemy import func, or_, desc
import datetime
import re
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.item import Item
from app.models.invoice import Invoice
from app.models.invoice_item import InvoiceItem
from app.models.payment import Payment
from app.models.cost_detail import CostDetail
from app.models.payment_distribution import PaymentDistribution
from app.extensions import db
from flask_login import login_required, current_user
from app.utils import check_project_permission, sanitize_input
from app.forms import InvoiceForm
from app import constants

invoice_bp = Blueprint("invoice", __name__, url_prefix='/invoices')

@invoice_bp.route("/project/<int:project_id>")
@login_required
def get_invoices_by_project(project_id):
    project = Project.query.get_or_404(project_id)
    check_project_permission(project)

    search_term = request.args.get('search', '')
    invoice_type_filter = request.args.get('type', '')
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    sort_by = request.args.get('sort_by', 'invoice_date')
    sort_order = request.args.get('sort_order', 'desc')

    query = Invoice.query.filter_by(project_id=project.id)

    if search_term:
        query = query.join(Contractor).filter(
            or_(
                Invoice.invoice_number.ilike(f"%{search_term}%"),
                Invoice.purchase_order_number.ilike(f"%{search_term}%"),
                Invoice.disbursement_order_number.ilike(f"%{search_term}%"),
                Contractor.name.ilike(f"%{search_term}%")
            )
        )
    if invoice_type_filter:
        query = query.filter(Invoice.invoice_type == invoice_type_filter)
    if start_date_str and end_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Invoice.invoice_date.between(start_date, end_date))
        except ValueError:
            flash("صيغة التاريخ غير صالحة.", "warning")
            
    if sort_by == 'invoice_number':
        order_column = Invoice.invoice_number
    else:
        order_column = Invoice.invoice_date

    if sort_order == 'asc':
        query = query.order_by(order_column.asc())
    else:
        query = query.order_by(order_column.desc())

    invoices = query.all()

    filters = {
        'search': search_term,
        'type': invoice_type_filter,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'sort_by': sort_by,
        'sort_order': sort_order
    }

    return render_template("invoices/index.html", 
                           project=project, 
                           invoices=invoices, 
                           filters=filters,
                           invoice_types=constants.INVOICE_TYPE_CHOICES)


@invoice_bp.route("/new/project/<int:project_id>", methods=["GET", "POST"])
@login_required
def new_invoice(project_id):
    project = Project.query.get_or_404(project_id)
    check_project_permission(project)
    
    form = InvoiceForm(project_id=project.id)
    next_invoice_number = "001" 

    if request.method == "GET":
        last_invoice_tuple = db.session.query(Invoice.invoice_number).filter_by(project_id=project_id).order_by(func.cast(func.regexp_replace(Invoice.invoice_number, '[^0-9]', '', 'g'), db.Integer).desc()).first()
        
        if last_invoice_tuple and last_invoice_tuple[0]:
            last_invoice_number = last_invoice_tuple[0]
            numbers = re.findall(r'(\d+)', last_invoice_number)
            if numbers:
                last_num_str = numbers[-1]
                next_num = int(last_num_str) + 1
                
                prefix = last_invoice_number.rsplit(last_num_str, 1)[0]
                
                next_invoice_number = prefix + str(next_num).zfill(len(last_num_str))
            else:
                 next_invoice_number = last_invoice_number + "-1"
        
        form.invoice_number.data = next_invoice_number
        
    form.contractor_id.choices = [(c.id, c.name) for c in Contractor.query.order_by(Contractor.name).all()]
    form.contractor_id.choices.insert(0, (0, '-- اختر المقاول أو المورد --'))

    if form.validate_on_submit():
        new_invoice_obj = Invoice()
        form.populate_obj(new_invoice_obj)
        new_invoice_obj.project_id = project.id
        new_invoice_obj.status = constants.INVOICE_STATUS_NEW
        
        db.session.add(new_invoice_obj)
        db.session.commit()
        flash("تم إنشاء المستخلص بنجاح. يمكنك الآن إضافة البنود إليه.", "success")
        return redirect(url_for("invoice.show_invoice", invoice_id=new_invoice_obj.id))
    
    return render_template("invoices/new.html", project=project, form=form, next_invoice_number=form.invoice_number.data)


@invoice_bp.route("/<int:invoice_id>/delete", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    check_project_permission(invoice.project)
    project_id = invoice.project_id
    if current_user.role != 'admin':
        abort(403)
    db.session.delete(invoice)
    db.session.commit()
    flash(f"تم حذف المستخلص رقم '{invoice.invoice_number}' وكل ما يتعلق به بنجاح.", "success")
    return redirect(url_for("invoice.get_invoices_by_project", project_id=project_id))

@invoice_bp.route("/<int:invoice_id>")
@login_required
def show_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    check_project_permission(invoice.project)
    added_main_items_subquery = db.session.query(InvoiceItem.item_id).filter(
        InvoiceItem.invoice_id == invoice.id,
        InvoiceItem.cost_detail_id.is_(None)
    )
    available_items = Item.query.filter(
        Item.project_id == invoice.project_id,
        Item.contractor_id == invoice.contractor_id,
        ~Item.id.in_(added_main_items_subquery)
    ).order_by(Item.item_number).all()
    added_cost_details_subquery = db.session.query(InvoiceItem.cost_detail_id).filter(
        InvoiceItem.invoice_id == invoice.id,
        InvoiceItem.cost_detail_id.isnot(None)
    )
    available_cost_details = CostDetail.query.filter(
        CostDetail.contractor_id == invoice.contractor_id,
        CostDetail.item.has(project_id=invoice.project_id),
        ~CostDetail.id.in_(added_cost_details_subquery)
    ).order_by(CostDetail.id).all()
    return render_template(
        "invoices/show.html", 
        invoice=invoice, 
        available_items=available_items, 
        available_cost_details=available_cost_details
    )

@invoice_bp.route("/<int:invoice_id>/add_item", methods=["POST"])
@login_required
def add_item_to_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    check_project_permission(invoice.project)
    selected_id_str = request.form.get("selected_item_id")
    quantity_str = request.form.get("quantity")
    if not selected_id_str or not quantity_str:
        flash("الرجاء اختيار بند وإدخال الكمية.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    try:
        new_quantity = float(quantity_str)
        if new_quantity <= 0:
            flash("يجب أن تكون الكمية أكبر من صفر.", "danger")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    except ValueError:
        flash("الكمية المدخلة غير صالحة.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    if selected_id_str.startswith('item_'):
        item_id = int(selected_id_str.split('_')[1])
        item = Item.query.get_or_404(item_id)
        max_quantity = item.actual_quantity
        if max_quantity is None or max_quantity <= 0:
            flash(f"لا يمكن فوترة البند '{item.item_number}' لعدم وجود 'كمية فعلية' مسجلة له.", "danger")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
        previously_invoiced_qty = db.session.query(func.sum(InvoiceItem.quantity)).filter(InvoiceItem.item_id == item.id, InvoiceItem.cost_detail_id.is_(None)).scalar() or 0.0
        if (previously_invoiced_qty + new_quantity) > max_quantity:
            remaining_qty = max_quantity - previously_invoiced_qty
            flash(f"كمية البند الرئيسي تتجاوز الحد المسموح. المتبقي: {remaining_qty:.2f}", "danger")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
        new_invoice_item = InvoiceItem(quantity=new_quantity, item=item)
        flash_message = f"تمت إضافة البند الرئيسي '{item.item_number}' للمستخلص."
    elif selected_id_str.startswith('detail_'):
        detail_id = int(selected_id_str.split('_')[1])
        cost_detail = CostDetail.query.get_or_404(detail_id)
        max_quantity = cost_detail.quantity
        previously_invoiced_qty = db.session.query(func.sum(InvoiceItem.quantity)).filter(InvoiceItem.cost_detail_id == detail_id).scalar() or 0.0
        if (previously_invoiced_qty + new_quantity) > max_quantity:
            remaining_qty = max_quantity - previously_invoiced_qty
            flash(f"كمية تفصيل التكلفة تتجاوز الحد المسموح. المتبقي: {remaining_qty:.2f}", "danger")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
        new_invoice_item = InvoiceItem(quantity=new_quantity, cost_detail=cost_detail)
        flash_message = f"تمت إضافة تفصيل التكلفة '{cost_detail.description}' للمستخلص."
    else:
        flash("اختيار غير صالح.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    new_invoice_item.invoice_id = invoice.id
    db.session.add(new_invoice_item)
    db.session.commit()
    flash(flash_message, "success")
    return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    
@invoice_bp.route("/<int:invoice_id>/add_payment", methods=["POST"])
@login_required
def add_payment_to_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    check_project_permission(invoice.project)

    payment_date_str = request.form.get("payment_date")
    description = sanitize_input(request.form.get("description"))
    reference_number = sanitize_input(request.form.get("reference_number"))

    if not payment_date_str:
        flash("تاريخ الدفعة حقل مطلوب.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))
    
    try:
        payment_date = datetime.datetime.strptime(payment_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        flash("صيغة التاريخ المدخلة غير صالحة.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

    distributions = []
    total_payment_amount = 0.0

    for key, value in request.form.items():
        if key.startswith("dist_item_"):
            try:
                invoice_item_id = int(key.split("_")[-1])
                amount = float(value) if value else 0.0

                if amount > 0:
                    invoice_item = InvoiceItem.query.get(invoice_item_id)
                    if not invoice_item or invoice_item.invoice_id != invoice_id:
                        flash(f"تم العثور على بند غير صالح في الدفعة (ID: {invoice_item_id}).", "danger")
                        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

                    if amount > round(invoice_item.remaining_amount, 2) + 0.001:
                        flash(f"المبلغ المدفوع للبند '{invoice_item.description[:30]}...' يتجاوز المبلغ المتبقي.", "danger")
                        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

                    distributions.append({'invoice_item': invoice_item, 'amount': amount})
                    total_payment_amount += amount
            
            except (ValueError, TypeError):
                flash("تم إدخال قيمة غير صالحة لأحد البنود.", "danger")
                return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

    if not distributions:
        flash("الرجاء إدخال مبلغ لدفعه لبند واحد على الأقل.", "warning")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

    try:
        # --- START: حساب رقم الدفعة التلقائي ---
        last_payment_number = db.session.query(func.max(Payment.payment_number)).filter_by(invoice_id=invoice.id).scalar() or 0
        new_payment_number = last_payment_number + 1
        # --- END: حساب رقم الدفعة التلقائي ---

        new_payment = Payment(
            invoice_id=invoice.id,
            amount=total_payment_amount,
            payment_date=payment_date,
            description=description,
            payment_number=new_payment_number,
            reference_number=reference_number
        )
        db.session.add(new_payment)
        db.session.flush()

        for dist_data in distributions:
            new_distribution = PaymentDistribution(
                payment_id=new_payment.id,
                invoice_item_id=dist_data['invoice_item'].id,
                amount=dist_data['amount']
            )
            db.session.add(new_distribution)

        db.session.commit()
        flash("تم تسجيل الدفعة وتوزيعها بنجاح.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"حدث خطأ أثناء حفظ الدفعة: {e}", "danger")

    return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

@invoice_bp.route("/payments/<int:payment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_payment(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    invoice = payment.invoice
    check_project_permission(invoice.project)

    if request.method == "POST":
        try:
            payment.payment_date = datetime.datetime.strptime(request.form.get("payment_date"), "%Y-%m-%d").date()
            payment.description = sanitize_input(request.form.get("description"))
            # --- START: تعديل الرقم المرجعي ---
            payment.reference_number = sanitize_input(request.form.get("reference_number"))
            # --- END: تعديل الرقم المرجعي ---

            new_total_amount = 0.0
            distributions_from_form = {} 

            for key, value in request.form.items():
                if key.startswith("dist_item_"):
                    invoice_item_id = int(key.split("_")[-1])
                    amount = float(value) if value else 0.0
                    
                    if amount < 0:
                        flash("لا يمكن إدخال مبلغ سالب.", "danger")
                        return redirect(url_for('invoice.edit_payment', payment_id=payment_id))
                    
                    invoice_item = InvoiceItem.query.get(invoice_item_id)
                    current_dist = next((d for d in payment.distributions if d.invoice_item_id == invoice_item_id), None)
                    old_amount_for_item = current_dist.amount if current_dist else 0
                    
                    max_allowed = invoice_item.remaining_amount + old_amount_for_item
                    
                    if amount > round(max_allowed, 2) + 0.001:
                        flash(f"المبلغ للبند '{invoice_item.description[:30]}...' يتجاوز المتبقي.", "danger")
                        dists_dict = {dist.invoice_item_id: dist.amount for dist in payment.distributions}
                        return render_template("invoices/edit_payment.html", payment=payment, invoice=invoice, dists=dists_dict)

                    if amount > 0:
                        distributions_from_form[invoice_item_id] = amount
                        new_total_amount += amount

            payment.amount = new_total_amount

            existing_dists_map = {d.invoice_item_id: d for d in payment.distributions}

            for item_id, amount in distributions_from_form.items():
                if item_id in existing_dists_map:
                    existing_dists_map[item_id].amount = amount
                else:
                    new_dist = PaymentDistribution(payment_id=payment.id, invoice_item_id=item_id, amount=amount)
                    db.session.add(new_dist)

            for item_id, dist in existing_dists_map.items():
                if item_id not in distributions_from_form:
                    db.session.delete(dist)
            
            db.session.commit()
            flash("تم تحديث الدفعة بنجاح.", "success")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice.id))

        except (ValueError, TypeError) as e:
            flash(f"البيانات المدخلة غير صالحة. {e}", "danger")
    
    dists_dict = {dist.invoice_item_id: dist.amount for dist in payment.distributions}
    return render_template("invoices/edit_payment.html", payment=payment, invoice=invoice, dists=dists_dict)

@invoice_bp.route("/payments/<int:payment_id>/delete", methods=["POST"])
@login_required
def delete_payment_from_invoice(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    invoice = payment.invoice
    check_project_permission(invoice.project)
    invoice_id = invoice.id
    db.session.delete(payment)
    db.session.commit()
    flash("تم حذف الدفعة بنجاح.", "success")
    return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

@invoice_bp.route("/items/<int:invoice_item_id>/delete", methods=["POST"])
@login_required
def delete_item_from_invoice(invoice_item_id):
    invoice_item = InvoiceItem.query.get_or_404(invoice_item_id)
    invoice = invoice_item.invoice
    check_project_permission(invoice.project)

    if invoice_item.distributions:
        flash("لا يمكن حذف بند تم توزيع دفعات عليه. يجب حذف الدفعات المرتبطة أولاً.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice.id))
    
    invoice_id = invoice.id
    db.session.delete(invoice_item)
    db.session.commit()
    flash("تم حذف البند من المستخلص بنجاح.", "success")
    return redirect(url_for('invoice.show_invoice', invoice_id=invoice_id))

@invoice_bp.route("/items/<int:invoice_item_id>/edit", methods=["GET", "POST"])
@login_required
def edit_item_from_invoice(invoice_item_id):
    invoice_item = InvoiceItem.query.get_or_404(invoice_item_id)
    invoice = invoice_item.invoice
    check_project_permission(invoice.project)

    if invoice_item.distributions:
        flash("لا يمكن تعديل كمية بند تم توزيع دفعات عليه.", "danger")
        return redirect(url_for('invoice.show_invoice', invoice_id=invoice.id))

    if request.method == "POST":
        quantity_str = request.form.get("quantity")
        try:
            quantity = float(quantity_str)
            if quantity <= 0:
                flash("يجب أن تكون الكمية أكبر من صفر.", "danger")
                return redirect(url_for('invoice.edit_item_from_invoice', invoice_item_id=invoice_item.id))
            
            invoice_item.quantity = quantity
            invoice_item.total_price = quantity * invoice_item.unit_price
            
            db.session.commit()
            flash("تم تحديث كمية البند بنجاح.", "success")
            return redirect(url_for('invoice.show_invoice', invoice_id=invoice.id))
        except (ValueError, TypeError):
            flash("الكمية المدخلة غير صالحة.", "danger")
    
    return render_template("invoices/edit_invoice_item.html", invoice_item=invoice_item)