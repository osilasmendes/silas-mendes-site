import os
from io import BytesIO
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-change-me")
database_url = os.getenv("DATABASE_URL", "").strip()
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///silas_mendes.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

PROFILE_UPLOAD_FOLDER = os.path.join(app.static_folder, "uploads", "profiles")
PROFILE_ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
PROFILE_MAX_FILE_SIZE = 3 * 1024 * 1024  # 3 MB
os.makedirs(PROFILE_UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para continuar."
login_manager.login_message_category = "warning"

WHATSAPP_NUMBER = os.getenv("WHATSAPP_NUMBER", "5581998925005")

DEFAULT_SERVICE_PRICES = {
    "Suporte técnico remoto": 5000,
    "Suporte técnico presencial": 10000,
    "Formatação e instalação sem backup": 10000,
    "Formatação e instalação com backup": 15000,
    "Criação de currículo": 300,
    "Faturas, boletos e documentos": 1000,
    "Contas de água e luz": 500,
}

PAYMENT_METHODS = ["Pix", "Cartão de crédito", "Cartão de débito"]


def format_brl(cents):
    if cents is None:
        return "—"
    value = cents / 100
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"



LOCAL_TIMEZONE = ZoneInfo("America/Recife")


def to_local_datetime(value):
    """Converte datetime salvo em UTC para o horário de Recife apenas na exibição."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.astimezone(LOCAL_TIMEZONE)


@app.template_filter("local_datetime")
def local_datetime_filter(value, fmt="%d/%m/%Y às %H:%M"):
    local_value = to_local_datetime(value)
    if local_value is None:
        return "—"
    return local_value.strftime(fmt)

def parse_brl_to_cents(value):
    """Converte entradas como 150,00 / 150.00 / 1.500,00 para centavos."""
    if value is None:
        return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if not text:
        return None

    try:
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        number = float(text)
        if number < 0:
            return None
        return int(round(number * 100))
    except (TypeError, ValueError):
        return None


class ServicePrice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_name = db.Column(db.String(160), unique=True, nullable=False)
    price_cents = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


def get_service_prices():
    """Retorna os preços atuais salvos pelo administrador."""
    prices = dict(DEFAULT_SERVICE_PRICES)
    rows = ServicePrice.query.order_by(ServicePrice.id.asc()).all()
    for row in rows:
        prices[row.service_name] = row.price_cents
    return prices


def allowed_profile_image(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in PROFILE_ALLOWED_EXTENSIONS
    )


def profile_image_size_ok(file_storage):
    """Valida o tamanho sem manter o arquivo inteiro em memória."""
    current_position = file_storage.stream.tell()
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(current_position)
    return 0 < size <= PROFILE_MAX_FILE_SIZE


def delete_profile_image(filename):
    if not filename:
        return
    safe_name = os.path.basename(filename)
    file_path = os.path.join(PROFILE_UPLOAD_FOLDER, safe_name)
    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_blocked = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    privacy_accepted_at = db.Column(db.DateTime, nullable=True)
    terms_accepted_at = db.Column(db.DateTime, nullable=True)

    # Informações opcionais do perfil
    address = db.Column(db.String(180), nullable=True)
    neighborhood = db.Column(db.String(120), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(2), nullable=True)
    postal_code = db.Column(db.String(12), nullable=True)
    profession = db.Column(db.String(120), nullable=True)
    company = db.Column(db.String(150), nullable=True)
    profile_image = db.Column(db.String(255), nullable=True)
    profile_image_data = db.Column(db.LargeBinary, nullable=True)
    profile_image_mime = db.Column(db.String(50), nullable=True)

    profile_photo_record = db.relationship(
        "ProfilePhoto",
        backref="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",
    )

    requests = db.relationship("ServiceRequest", backref="customer", lazy=True)
    quotes = db.relationship("QuoteRequest", backref="customer", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ProfilePhoto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    image_data = db.Column(db.LargeBinary, nullable=False)
    mime_type = db.Column(db.String(50), nullable=False, default="image/jpeg")
    filename = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )




class ServiceRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    service = db.Column(db.String(150), nullable=False)
    details = db.Column(db.Text, nullable=False)
    preferred_contact = db.Column(db.String(50), default="WhatsApp")
    price_cents = db.Column(db.Integer, nullable=True)
    payment_method = db.Column(db.String(40), nullable=True)
    status = db.Column(db.String(40), default="Recebida", nullable=False)
    cancellation_reason = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QuoteRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    project_type = db.Column(db.String(100), nullable=False)
    project_name = db.Column(db.String(150), nullable=True)
    description = db.Column(db.Text, nullable=False)
    budget = db.Column(db.String(80), nullable=True)
    deadline = db.Column(db.String(80), nullable=True)
    final_price_cents = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(40), default="Recebida", nullable=False)
    cancellation_reason = db.Column(db.Text, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def total_received_cents(self):
        return sum((payment.amount_cents or 0) for payment in self.payments)

    @property
    def balance_due_cents(self):
        if self.final_price_cents is None:
            return None
        return max(self.final_price_cents - self.total_received_cents, 0)


class ProjectPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quote_request_id = db.Column(
        db.Integer, db.ForeignKey("quote_request.id"), nullable=False
    )
    amount_cents = db.Column(db.Integer, nullable=False)
    payment_method = db.Column(db.String(40), nullable=False)
    note = db.Column(db.String(250), nullable=True)
    paid_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    quote = db.relationship(
        "QuoteRequest",
        backref=db.backref(
            "payments",
            lazy=True,
            cascade="all, delete-orphan",
            order_by="ProjectPayment.paid_at",
        ),
    )


class ServiceReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_request_id = db.Column(
        db.Integer, db.ForeignKey("service_request.id"), unique=True, nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    rating_attendance = db.Column(db.Integer, nullable=False)
    rating_site = db.Column(db.Integer, nullable=False)
    rating_service = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    testimonial_status = db.Column(db.String(20), default="Pendente", nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    customer = db.relationship("User", backref=db.backref("service_reviews", lazy=True))
    service_request = db.relationship(
        "ServiceRequest", backref=db.backref("review", uselist=False)
    )

    @property
    def average_rating(self):
        return round(
            (self.rating_attendance + self.rating_site + self.rating_service) / 3, 1
        )


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user and user.is_blocked:
        return None
    return user


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Acesso restrito ao administrador.", "danger")
            return redirect(url_for("index"))
        return view_func(*args, **kwargs)
    return wrapped


def wa_link(message):
    return f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(message)}"


@app.context_processor
def inject_globals():
    return {
        "current_year": datetime.now().year,
        "service_prices": get_service_prices(),
        "format_brl": format_brl,
    }


@app.route("/")
def index():
    projects = [
        {
            "title": "Laura Designer",
            "category": "Plataforma de agendamento",
            "description": "Site profissional para apresentação de trabalhos, cadastro e login de clientes, agendamentos, avaliações e painel administrativo.",
            "tech": "Flask • Responsivo • Gestão",
            "images": ["img/laura-home.png", "img/laura-login.png"],
        },
        {
            "title": "BuscarTag",
            "category": "Sistema web",
            "description": "Ferramenta para pesquisa e organização inteligente de TAGs e respostas operacionais, com interface própria para agilizar atendimentos.",
            "tech": "Python • Flask • Banco de dados",
            "images": ["img/buscartag.png"],
        },
        {
            "title": "Soluções sob medida",
            "category": "Sites e sistemas",
            "description": "Projetos personalizados para profissionais, pequenos negócios e rotinas internas.",
            "tech": "Automação • Web • Suporte",
            "images": [],
        },
    ]
    approved_reviews = (
        ServiceReview.query.filter_by(testimonial_status="Aprovado")
        .order_by(ServiceReview.reviewed_at.desc(), ServiceReview.created_at.desc())
        .limit(6)
        .all()
    )
    return render_template(
        "index.html",
        projects=projects,
        approved_reviews=approved_reviews,
    )


@app.route("/privacidade")
def privacy_policy():
    return render_template("privacy.html")


@app.route("/termos")
def terms_of_use():
    return render_template("terms.html")


@app.route("/cadastro", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("client_dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        accept_privacy = request.form.get("accept_privacy") == "yes"
        accept_terms = request.form.get("accept_terms") == "yes"

        if not name or not email or not password:
            flash("Preencha os campos obrigatórios.", "danger")
        elif not accept_privacy or not accept_terms:
            flash("Para criar a conta, é necessário aceitar a Política de Privacidade e os Termos de Uso.", "danger")
        elif password != confirm:
            flash("As senhas não coincidem.", "danger")
        elif len(password) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("Já existe uma conta com este e-mail.", "warning")
        else:
            accepted_at = datetime.utcnow()
            user = User(
                name=name,
                email=email,
                phone=phone,
                privacy_accepted_at=accepted_at,
                terms_accepted_at=accepted_at,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash("Conta criada com sucesso!", "success")
            return redirect(url_for("client_dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin_dashboard" if current_user.is_admin else "client_dashboard"))

    if request.method == "POST":
        identifier = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        admin_username = os.getenv("ADMIN_USERNAME", "ADMIN").strip()
        admin_email = os.getenv("ADMIN_EMAIL", "admin@silasmendes.local").strip().lower()

        # O administrador pode entrar usando o usuário definido no .env
        # (ex.: ADMIN) ou usando o e-mail administrativo. Clientes entram pelo e-mail.
        if identifier.lower() == admin_username.lower():
            user = User.query.filter_by(email=admin_email).first()
        else:
            user = User.query.filter_by(email=identifier.lower()).first()

        if user and user.is_blocked:
            flash("Esta conta está bloqueada. Entre em contato com o suporte.", "danger")
        elif user and user.check_password(password):
            login_user(user)
            flash("Login realizado com sucesso.", "success")

            if not user.is_admin:
                pending_review_count = (
                    ServiceRequest.query
                    .filter(
                        ServiceRequest.user_id == user.id,
                        ServiceRequest.status.in_(["Concluída", "Concluído"]),
                        ~ServiceRequest.id.in_(
                            db.session.query(ServiceReview.service_request_id)
                        ),
                    )
                    .count()
                )
                if pending_review_count:
                    flash(
                        f"Você tem {pending_review_count} atendimento(s) concluído(s) aguardando avaliação.",
                        "info",
                    )

            return redirect(url_for("admin_dashboard" if user.is_admin else "client_dashboard"))
        else:
            flash("Usuário/e-mail ou senha inválidos.", "danger")

    return render_template("login.html")


@app.route("/sair")
@login_required
def logout():
    logout_user()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("index"))


@app.route("/cliente")
@login_required
def client_dashboard():
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    all_requests = ServiceRequest.query.filter_by(
        user_id=current_user.id
    ).order_by(ServiceRequest.created_at.desc()).all()

    all_quotes = QuoteRequest.query.filter_by(
        user_id=current_user.id
    ).order_by(QuoteRequest.created_at.desc()).all()

    requests_list = [item for item in all_requests if item.status != "Cancelada"]
    quotes = [item for item in all_quotes if item.status != "Cancelada"]

    cancelled_requests = [item for item in all_requests if item.status == "Cancelada"]
    cancelled_quotes = [item for item in all_quotes if item.status == "Cancelada"]

    pending_reviews = [
        item
        for item in requests_list
        if item.status in ["Concluída", "Concluído"] and item.review is None
    ]

    return render_template(
        "client_dashboard.html",
        requests_list=requests_list,
        quotes=quotes,
        cancelled_requests=cancelled_requests,
        cancelled_quotes=cancelled_quotes,
        pending_reviews=pending_reviews,
    )


@app.route("/cliente/cadastro", methods=["GET", "POST"])
@login_required
def client_profile():
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        neighborhood = request.form.get("neighborhood", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip().upper()
        postal_code = request.form.get("postal_code", "").strip()
        profession = request.form.get("profession", "").strip()
        company = request.form.get("company", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        profile_photo = request.files.get("profile_photo")

        if not name or not email:
            flash("Nome e e-mail são obrigatórios.", "danger")
        elif len(state) not in (0, 2):
            flash("Informe a UF com 2 letras, por exemplo: PE.", "danger")
        else:
            existing = User.query.filter(
                User.email == email,
                User.id != current_user.id,
            ).first()

            if existing:
                flash("Este e-mail já está sendo usado por outra conta.", "warning")
            elif new_password and len(new_password) < 6:
                flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
            elif new_password and new_password != confirm_password:
                flash("A confirmação da nova senha não coincide.", "danger")
            elif profile_photo and profile_photo.filename and not allowed_profile_image(profile_photo.filename):
                flash("A foto deve estar em JPG, JPEG, PNG ou WEBP.", "danger")
            else:
                image_data = None
                image_extension = None
                image_mime = None
                image_filename = None

                if profile_photo and profile_photo.filename:
                    image_data = profile_photo.read()

                    if not image_data:
                        flash("A imagem selecionada está vazia.", "danger")
                        return render_template("client_profile.html")

                    if len(image_data) > PROFILE_MAX_FILE_SIZE:
                        flash("A foto deve ter no máximo 3 MB.", "danger")
                        return render_template("client_profile.html")

                    image_extension = profile_photo.filename.rsplit(".", 1)[1].lower()
                    mime_map = {
                        "jpg": "image/jpeg",
                        "jpeg": "image/jpeg",
                        "png": "image/png",
                        "webp": "image/webp",
                    }
                    image_mime = mime_map.get(image_extension, "image/jpeg")
                    image_filename = secure_filename(profile_photo.filename)

                try:
                    current_user.name = name
                    current_user.email = email
                    current_user.phone = phone or None
                    current_user.address = address or None
                    current_user.neighborhood = neighborhood or None
                    current_user.city = city or None
                    current_user.state = state or None
                    current_user.postal_code = postal_code or None
                    current_user.profession = profession or None
                    current_user.company = company or None

                    if new_password:
                        current_user.set_password(new_password)

                    # Se o cliente escolheu uma nova foto, salva na MESMA operação.
                    if image_data is not None:
                        photo_record = ProfilePhoto.query.filter_by(
                            user_id=current_user.id
                        ).first()

                        if photo_record is None:
                            photo_record = ProfilePhoto(
                                user_id=current_user.id,
                                image_data=image_data,
                                mime_type=image_mime,
                                filename=image_filename,
                            )
                            db.session.add(photo_record)
                        else:
                            photo_record.image_data = image_data
                            photo_record.mime_type = image_mime
                            photo_record.filename = image_filename
                            photo_record.updated_at = datetime.utcnow()

                        current_user.profile_image = (
                            f"photo_{current_user.id}_{uuid4().hex}.{image_extension}"
                        )

                    db.session.commit()
                    db.session.expire_all()

                    # Confirma no banco que a foto realmente ficou vinculada ao usuário.
                    if image_data is not None:
                        saved_photo = ProfilePhoto.query.filter_by(
                            user_id=current_user.id
                        ).first()

                        if saved_photo is None or not saved_photo.image_data:
                            flash(
                                "Os dados foram salvos, mas houve um problema ao persistir a foto.",
                                "danger",
                            )
                            return redirect(url_for("client_profile"))

                    flash("Seu cadastro foi atualizado com sucesso.", "success")
                    return redirect(url_for("client_profile"))

                except Exception:
                    db.session.rollback()
                    flash(
                        "Não foi possível salvar as alterações. Tente novamente.",
                        "danger",
                    )

    return render_template("client_profile.html")


@app.route("/cliente/foto", methods=["POST"])
@login_required
def upload_profile_photo():
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    profile_photo = request.files.get("profile_photo")

    if not profile_photo or not profile_photo.filename:
        flash("Selecione uma foto antes de enviar.", "warning")
        return redirect(url_for("client_profile"))

    if not allowed_profile_image(profile_photo.filename):
        flash("A foto deve estar em JPG, JPEG, PNG ou WEBP.", "danger")
        return redirect(url_for("client_profile"))

    image_data = profile_photo.read()

    if not image_data:
        flash("A imagem selecionada está vazia.", "danger")
        return redirect(url_for("client_profile"))

    if len(image_data) > PROFILE_MAX_FILE_SIZE:
        flash("A foto deve ter no máximo 3 MB.", "danger")
        return redirect(url_for("client_profile"))

    extension = profile_photo.filename.rsplit(".", 1)[1].lower()
    mime_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }
    mime_type = mime_map.get(extension, "image/jpeg")

    photo_record = ProfilePhoto.query.filter_by(user_id=current_user.id).first()

    if photo_record is None:
        photo_record = ProfilePhoto(
            user_id=current_user.id,
            image_data=image_data,
            mime_type=mime_type,
            filename=secure_filename(profile_photo.filename),
        )
        db.session.add(photo_record)
    else:
        photo_record.image_data = image_data
        photo_record.mime_type = mime_type
        photo_record.filename = secure_filename(profile_photo.filename)
        photo_record.updated_at = datetime.utcnow()

    # Mantém marcador para compatibilidade visual/cache.
    current_user.profile_image = f"photo_{current_user.id}_{uuid4().hex}.{extension}"

    db.session.commit()

    # Verificação real depois do commit.
    db.session.expire_all()
    saved_photo = ProfilePhoto.query.filter_by(user_id=current_user.id).first()

    if saved_photo is None or not saved_photo.image_data:
        flash("A foto não permaneceu salva no banco. Tente novamente.", "danger")
        return redirect(url_for("client_profile"))

    flash("Foto de perfil salva permanentemente na sua conta.", "success")
    return redirect(url_for("client_profile"))


@app.route("/cliente/foto/<int:user_id>")
@login_required
def profile_photo_file(user_id):
    if not current_user.is_admin and current_user.id != user_id:
        return "", 403

    photo_record = ProfilePhoto.query.filter_by(user_id=user_id).first()

    if photo_record is None or not photo_record.image_data:
        return "", 404

    return send_file(
        BytesIO(photo_record.image_data),
        mimetype=photo_record.mime_type or "image/jpeg",
        max_age=0,
        conditional=False,
    )


@app.route("/cliente/foto/remover", methods=["POST"])
@login_required
def remove_profile_photo():
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    photo_record = ProfilePhoto.query.filter_by(user_id=current_user.id).first()

    if photo_record:
        db.session.delete(photo_record)
        current_user.profile_image = None
        current_user.profile_image_data = None
        current_user.profile_image_mime = None
        db.session.commit()
        flash("Foto de perfil removida.", "success")
    else:
        flash("Você ainda não possui uma foto de perfil.", "info")

    return redirect(url_for("client_profile"))


@app.route("/avaliar/<int:item_id>", methods=["GET", "POST"])
@login_required
def review_service(item_id):
    if current_user.is_admin:
        return redirect(url_for("admin_dashboard"))

    item = ServiceRequest.query.get_or_404(item_id)

    if item.user_id != current_user.id:
        flash("Você não pode avaliar este atendimento.", "danger")
        return redirect(url_for("client_dashboard"))

    if item.status not in ["Concluída", "Concluído"]:
        flash("A avaliação fica disponível depois que o atendimento for concluído.", "warning")
        return redirect(url_for("client_dashboard"))

    existing = ServiceReview.query.filter_by(service_request_id=item.id).first()
    if existing:
        flash("Este atendimento já foi avaliado. Obrigado!", "info")
        return redirect(url_for("client_dashboard"))

    if request.method == "POST":
        try:
            rating_attendance = int(request.form.get("rating_attendance", "0"))
            rating_site = int(request.form.get("rating_site", "0"))
            rating_service = int(request.form.get("rating_service", "0"))
        except ValueError:
            rating_attendance = rating_site = rating_service = 0

        comment = request.form.get("comment", "").strip()

        ratings = [rating_attendance, rating_site, rating_service]
        if not all(1 <= value <= 5 for value in ratings):
            flash("Escolha de 1 a 5 estrelas em todas as avaliações.", "danger")
        elif len(comment) > 1500:
            flash("O comentário deve ter no máximo 1500 caracteres.", "danger")
        else:
            review = ServiceReview(
                service_request_id=item.id,
                user_id=current_user.id,
                rating_attendance=rating_attendance,
                rating_site=rating_site,
                rating_service=rating_service,
                comment=comment or None,
                testimonial_status="Pendente",
            )
            db.session.add(review)
            db.session.commit()
            flash(
                "Avaliação enviada com sucesso. Obrigado! Seu comentário poderá aparecer nos depoimentos após aprovação.",
                "success",
            )
            return redirect(url_for("client_dashboard"))

    return render_template("review_service.html", item=item)


@app.route("/admin/avaliacoes")
@login_required
@admin_required
def admin_reviews():
    reviews = ServiceReview.query.order_by(ServiceReview.created_at.desc()).all()
    pending_count = ServiceReview.query.filter_by(testimonial_status="Pendente").count()
    return render_template(
        "admin_reviews.html",
        reviews=reviews,
        pending_count=pending_count,
    )


@app.route("/admin/avaliacoes/<int:review_id>/<action>", methods=["POST"])
@login_required
@admin_required
def moderate_review(review_id, action):
    review = ServiceReview.query.get_or_404(review_id)

    if action == "aprovar":
        review.testimonial_status = "Aprovado"
        review.reviewed_at = datetime.utcnow()
        flash("Depoimento aprovado e publicado no site.", "success")
    elif action == "rejeitar":
        review.testimonial_status = "Não publicar"
        review.reviewed_at = datetime.utcnow()
        flash("Avaliação mantida no sistema, mas o comentário não será publicado.", "info")
    else:
        flash("Ação inválida.", "danger")

    db.session.commit()
    return redirect(url_for("admin_reviews"))


@app.route("/escolher-solicitacao")
def request_choice():
    return render_template("request_choice.html")


@app.route("/atendimento", methods=["GET", "POST"])
@login_required
def service_request():
    # Serviços técnicos foram descontinuados. Mantemos a rota apenas
    # para compatibilidade com links antigos, sem apagar registros históricos.
    return redirect(url_for("request_choice"))


@app.route("/orcamento", methods=["GET", "POST"])
@login_required
def quote_request():
    # Ao acessar /orcamento sem escolher um tipo de projeto,
    # primeiro mostramos a tela para escolher entre Site e Sistema.
    if request.method == "GET" and not request.args.get("tipo", "").strip():
        return redirect(url_for("request_choice"))

    if request.method == "POST":
        project_type = request.form.get("project_type", "").strip()
        project_name = request.form.get("project_name", "").strip()
        description = request.form.get("description", "").strip()
        budget = request.form.get("budget", "").strip()
        deadline = request.form.get("deadline", "").strip()

        if not project_type or not description:
            flash("Informe o tipo do projeto e uma descrição.", "danger")
        else:
            quote = QuoteRequest(
                user_id=current_user.id,
                project_type=project_type,
                project_name=project_name,
                description=description,
                budget=budget,
                deadline=deadline,
            )
            db.session.add(quote)
            db.session.commit()

            message = (
                f"Olá, Silas! Nova solicitação de orçamento #{quote.id}.\n"
                f"Cliente: {current_user.name}\n"
                f"Projeto: {project_name or 'Sem nome definido'}\n"
                f"Tipo: {project_type}\n"
                f"Descrição: {description}\n"
                f"Orçamento estimado pelo cliente: {budget or 'Não informado'}\n"
                f"Prazo desejado: {deadline or 'Não informado'}"
            )
            return redirect(wa_link(message))

    selected_project_type = request.args.get("tipo", "").strip()
    return render_template("quote_request.html", selected_project_type=selected_project_type)


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    stats = {
        "users": User.query.filter_by(is_admin=False).count(),
        "requests": ServiceRequest.query.count(),
        "quotes": QuoteRequest.query.count(),
        "open": ServiceRequest.query.filter(ServiceRequest.status.in_(["Recebida", "Em andamento"])).count()
        + QuoteRequest.query.filter(QuoteRequest.status.in_(["Recebida", "Em andamento"])).count(),
        "reviews_pending": ServiceReview.query.filter_by(testimonial_status="Pendente").count(),
        "cancelled": ServiceRequest.query.filter_by(status="Cancelada").count()
        + QuoteRequest.query.filter_by(status="Cancelada").count(),
    }

    completed_services = ServiceRequest.query.filter_by(status="Concluída").all()
    completed_quotes = QuoteRequest.query.filter_by(status="Concluída").all()
    stats["sales_total_cents"] = sum((item.price_cents or 0) for item in completed_services) + sum(
        (item.final_price_cents or 0) for item in completed_quotes
    )

    now = datetime.utcnow()
    stats["sales_month_cents"] = sum(
        (item.price_cents or 0)
        for item in completed_services
        if (item.completed_at or item.updated_at or item.created_at).year == now.year
        and (item.completed_at or item.updated_at or item.created_at).month == now.month
    ) + sum(
        (item.final_price_cents or 0)
        for item in completed_quotes
        if (item.completed_at or item.updated_at or item.created_at).year == now.year
        and (item.completed_at or item.updated_at or item.created_at).month == now.month
    )

    project_payments = ProjectPayment.query.all()
    stats["project_received_total_cents"] = sum((payment.amount_cents or 0) for payment in project_payments)
    stats["project_received_month_cents"] = sum(
        (payment.amount_cents or 0)
        for payment in project_payments
        if payment.paid_at.year == now.year and payment.paid_at.month == now.month
    )
    open_project_quotes = QuoteRequest.query.filter(
        QuoteRequest.status != "Cancelada",
        QuoteRequest.final_price_cents.isnot(None),
    ).all()
    stats["project_pending_cents"] = sum((item.balance_due_cents or 0) for item in open_project_quotes)

    latest_requests = ServiceRequest.query.order_by(ServiceRequest.created_at.desc()).limit(6).all()
    latest_quotes = QuoteRequest.query.order_by(QuoteRequest.created_at.desc()).limit(6).all()
    return render_template("admin_dashboard.html", stats=stats, latest_requests=latest_requests, latest_quotes=latest_quotes)


@app.route("/admin/precos", methods=["GET", "POST"])
@login_required
@admin_required
def admin_prices():
    # A tabela de preços dos antigos serviços técnicos não é mais utilizada.
    # Mantemos a rota apenas para compatibilidade, sem apagar dados históricos.
    flash("Os serviços técnicos foram descontinuados. Agora o site trabalha apenas com sites e sistemas.", "info")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/solicitacoes")
@login_required
@admin_required
def admin_requests():
    requests_list = ServiceRequest.query.filter(
        ServiceRequest.status != "Cancelada"
    ).order_by(ServiceRequest.created_at.desc()).all()

    quotes = QuoteRequest.query.filter(
        QuoteRequest.status != "Cancelada"
    ).order_by(QuoteRequest.created_at.desc()).all()

    cancelled_requests = ServiceRequest.query.filter_by(
        status="Cancelada"
    ).order_by(ServiceRequest.cancelled_at.desc(), ServiceRequest.created_at.desc()).all()

    cancelled_quotes = QuoteRequest.query.filter_by(
        status="Cancelada"
    ).order_by(QuoteRequest.cancelled_at.desc(), QuoteRequest.created_at.desc()).all()

    return render_template(
        "admin_requests.html",
        requests_list=requests_list,
        quotes=quotes,
        cancelled_requests=cancelled_requests,
        cancelled_quotes=cancelled_quotes,
    )


@app.route("/admin/atendimento/<int:item_id>/status", methods=["POST"])
@login_required
@admin_required
def update_service_status(item_id):
    item = ServiceRequest.query.get_or_404(item_id)
    allowed = ["Recebida", "Em andamento", "Aguardando cliente", "Concluída", "Cancelada"]
    status = request.form.get("status", "").strip()
    cancellation_reason = request.form.get("cancellation_reason", "").strip()

    if status not in allowed:
        flash("Status inválido.", "danger")
        return redirect(url_for("admin_requests"))

    if status == "Cancelada" and not cancellation_reason:
        flash("Informe o motivo do cancelamento antes de cancelar a ordem.", "danger")
        return redirect(url_for("admin_requests"))

    old_status = item.status
    item.status = status

    if status == "Concluída":
        if old_status != "Concluída" or not item.completed_at:
            item.completed_at = datetime.utcnow()
        item.cancellation_reason = None
        item.cancelled_at = None
    elif status == "Cancelada":
        item.cancellation_reason = cancellation_reason
        if old_status != "Cancelada" or not item.cancelled_at:
            item.cancelled_at = datetime.utcnow()
    else:
        if old_status == "Cancelada":
            item.cancellation_reason = None
            item.cancelled_at = None

    db.session.commit()
    flash("Status do atendimento atualizado.", "success")
    return redirect(url_for("admin_requests"))


@app.route("/admin/orcamento/<int:item_id>/status", methods=["POST"])
@login_required
@admin_required
def update_quote_status(item_id):
    item = QuoteRequest.query.get_or_404(item_id)
    allowed = ["Recebida", "Em andamento", "Proposta enviada", "Aprovada", "Concluída", "Cancelada"]
    status = request.form.get("status", "").strip()
    cancellation_reason = request.form.get("cancellation_reason", "").strip()
    final_price_raw = request.form.get("final_price", "").strip()

    if status not in allowed:
        flash("Status inválido.", "danger")
        return redirect(url_for("admin_requests"))

    final_price_cents = parse_brl_to_cents(final_price_raw) if final_price_raw else item.final_price_cents

    if status == "Concluída" and not final_price_cents:
        flash("Informe o valor final do projeto antes de marcar como concluído.", "danger")
        return redirect(url_for("admin_requests"))

    if status == "Cancelada" and not cancellation_reason:
        flash("Informe o motivo do cancelamento antes de cancelar a ordem.", "danger")
        return redirect(url_for("admin_requests"))

    old_status = item.status
    item.status = status

    if final_price_raw:
        parsed = parse_brl_to_cents(final_price_raw)
        if parsed is None:
            flash("Valor final inválido.", "danger")
            return redirect(url_for("admin_requests"))
        if parsed < item.total_received_cents:
            flash(
                f"O valor final não pode ser menor que o total já recebido ({format_brl(item.total_received_cents)}).",
                "danger",
            )
            return redirect(url_for("admin_requests"))
        item.final_price_cents = parsed

    if status == "Concluída":
        if old_status != "Concluída" or not item.completed_at:
            item.completed_at = datetime.utcnow()
        item.cancellation_reason = None
        item.cancelled_at = None
    elif status == "Cancelada":
        item.cancellation_reason = cancellation_reason
        if old_status != "Cancelada" or not item.cancelled_at:
            item.cancelled_at = datetime.utcnow()
    else:
        if old_status == "Cancelada":
            item.cancellation_reason = None
            item.cancelled_at = None

    db.session.commit()
    flash("Status do orçamento atualizado.", "success")
    return redirect(url_for("admin_requests"))


@app.route("/admin/orcamento/<int:item_id>/pagamento", methods=["POST"])
@login_required
@admin_required
def add_project_payment(item_id):
    item = QuoteRequest.query.get_or_404(item_id)

    amount_raw = request.form.get("amount", "").strip()
    payment_method = request.form.get("payment_method", "").strip()
    note = request.form.get("note", "").strip()

    amount_cents = parse_brl_to_cents(amount_raw)
    allowed_methods = ["Pix", "Cartão de crédito", "Cartão de débito", "Dinheiro", "Transferência", "Outro"]

    if amount_cents is None or amount_cents <= 0:
        flash("Informe um valor de pagamento válido.", "danger")
        return redirect(url_for("admin_requests"))

    if payment_method not in allowed_methods:
        flash("Selecione uma forma de pagamento válida.", "danger")
        return redirect(url_for("admin_requests"))

    if item.final_price_cents is None:
        flash("Defina primeiro o valor final do projeto.", "warning")
        return redirect(url_for("admin_requests"))

    if item.total_received_cents + amount_cents > item.final_price_cents:
        flash(
            f"O pagamento ultrapassa o valor final do projeto. Saldo atual: {format_brl(item.balance_due_cents)}.",
            "danger",
        )
        return redirect(url_for("admin_requests"))

    payment = ProjectPayment(
        quote_request_id=item.id,
        amount_cents=amount_cents,
        payment_method=payment_method,
        note=note or None,
    )
    db.session.add(payment)
    db.session.commit()

    flash(
        f"Pagamento de {format_brl(amount_cents)} registrado no projeto #{item.id}.",
        "success",
    )
    return redirect(url_for("admin_requests"))


@app.route("/admin/relatorios")
@login_required
@admin_required
def admin_reports():
    completed_services = ServiceRequest.query.filter_by(status="Concluída").all()
    completed_quotes = QuoteRequest.query.filter_by(status="Concluída").all()
    cancelled_services = ServiceRequest.query.filter_by(status="Cancelada").all()
    cancelled_quotes = QuoteRequest.query.filter_by(status="Cancelada").all()
    project_payments = ProjectPayment.query.order_by(ProjectPayment.paid_at.desc()).all()

    # Extrato de vendas concluídas
    completed_entries = []
    for item in completed_services:
        completed_entries.append({
            "type": "Serviço",
            "id": item.id,
            "customer": item.customer,
            "description": item.service,
            "value_cents": item.price_cents or 0,
            "received_cents": item.price_cents or 0,
            "pending_cents": 0,
            "payment_method": item.payment_method or "Não informado",
            "date": item.completed_at or item.updated_at or item.created_at,
        })

    for item in completed_quotes:
        completed_entries.append({
            "type": "Projeto",
            "id": item.id,
            "customer": item.customer,
            "description": item.project_name or item.project_type,
            "value_cents": item.final_price_cents or 0,
            "received_cents": item.total_received_cents,
            "pending_cents": item.balance_due_cents or 0,
            "payment_method": "Pagamentos registrados",
            "date": item.completed_at or item.updated_at or item.created_at,
        })

    completed_entries.sort(key=lambda entry: entry["date"], reverse=True)

    # Extrato de cancelamentos
    cancelled_entries = []
    for item in cancelled_services:
        cancelled_entries.append({
            "type": "Serviço",
            "id": item.id,
            "customer": item.customer,
            "description": item.service,
            "value_cents": item.price_cents or 0,
            "reason": item.cancellation_reason or "Motivo não informado",
            "date": item.cancelled_at or item.updated_at or item.created_at,
        })

    for item in cancelled_quotes:
        cancelled_entries.append({
            "type": "Projeto",
            "id": item.id,
            "customer": item.customer,
            "description": item.project_name or item.project_type,
            "value_cents": item.final_price_cents or 0,
            "reason": item.cancellation_reason or "Motivo não informado",
            "date": item.cancelled_at or item.updated_at or item.created_at,
        })

    cancelled_entries.sort(key=lambda entry: entry["date"], reverse=True)

    # Vendas por mês: ordens concluídas
    sales_by_month = {}
    for entry in completed_entries:
        key = entry["date"].strftime("%Y-%m")
        if key not in sales_by_month:
            sales_by_month[key] = {"count": 0, "sold_cents": 0}
        sales_by_month[key]["count"] += 1
        sales_by_month[key]["sold_cents"] += entry["value_cents"]

    # Recebimentos por mês:
    # serviços concluídos são considerados recebidos na conclusão;
    # projetos usam as movimentações financeiras registradas.
    receipts_by_month = {}
    for item in completed_services:
        date = item.completed_at or item.updated_at or item.created_at
        key = date.strftime("%Y-%m")
        receipts_by_month[key] = receipts_by_month.get(key, 0) + (item.price_cents or 0)

    for payment in project_payments:
        key = payment.paid_at.strftime("%Y-%m")
        receipts_by_month[key] = receipts_by_month.get(key, 0) + (payment.amount_cents or 0)

    month_names = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
        5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
        9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
    }

    month_keys = sorted(set(sales_by_month) | set(receipts_by_month), reverse=True)
    monthly_rows = []
    for key in month_keys:
        year, month = map(int, key.split("-"))
        sold_cents = sales_by_month.get(key, {}).get("sold_cents", 0)
        received_cents = receipts_by_month.get(key, 0)
        monthly_rows.append({
            "key": key,
            "label": f"{month_names[month]} de {year}",
            "count": sales_by_month.get(key, {}).get("count", 0),
            "sold_cents": sold_cents,
            "received_cents": received_cents,
        })

    total_sales_cents = sum(entry["value_cents"] for entry in completed_entries)
    total_received_cents = (
        sum((item.price_cents or 0) for item in completed_services)
        + sum((payment.amount_cents or 0) for payment in project_payments)
    )

    active_projects = QuoteRequest.query.filter(
        QuoteRequest.status != "Cancelada",
        QuoteRequest.final_price_cents.isnot(None),
    ).all()
    total_pending_cents = sum((item.balance_due_cents or 0) for item in active_projects)

    current_key = datetime.utcnow().strftime("%Y-%m")
    current_month_sales_cents = sales_by_month.get(current_key, {}).get("sold_cents", 0)
    current_month_received_cents = receipts_by_month.get(current_key, 0)

    project_payment_entries = []
    for payment in project_payments:
        project_payment_entries.append({
            "id": payment.id,
            "quote": payment.quote,
            "customer": payment.quote.customer,
            "amount_cents": payment.amount_cents,
            "payment_method": payment.payment_method,
            "note": payment.note,
            "date": payment.paid_at,
        })

    return render_template(
        "admin_reports.html",
        completed_entries=completed_entries,
        cancelled_entries=cancelled_entries,
        project_payment_entries=project_payment_entries,
        monthly_rows=monthly_rows,
        total_sales_cents=total_sales_cents,
        total_received_cents=total_received_cents,
        total_pending_cents=total_pending_cents,
        current_month_sales_cents=current_month_sales_cents,
        current_month_received_cents=current_month_received_cents,
        completed_count=len(completed_entries),
        cancelled_count=len(cancelled_entries),
    )


@app.route("/admin/usuarios", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        user_type = request.form.get("user_type", "client")
        status = request.form.get("status", "active")

        if not name or not email or not password:
            flash("Preencha nome, e-mail e senha.", "danger")
        elif len(password) < 6:
            flash("A senha deve ter pelo menos 6 caracteres.", "danger")
        elif password != confirm_password:
            flash("As senhas não coincidem.", "danger")
        elif user_type not in ["client", "admin"]:
            flash("Tipo de usuário inválido.", "danger")
        elif status not in ["active", "blocked"]:
            flash("Status de usuário inválido.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("Já existe um usuário com esse e-mail.", "warning")
        else:
            user = User(
                name=name,
                email=email,
                phone=phone,
                is_admin=(user_type == "admin"),
                is_blocked=(status == "blocked"),
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("Novo usuário cadastrado com sucesso.", "success")
            return redirect(url_for("admin_users"))

    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin_users.html", users=users)


@app.route("/admin/usuarios/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_user_detail(user_id):
    user = User.query.get_or_404(user_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        state = request.form.get("state", "").strip().upper()
        profession = request.form.get("profession", "").strip()
        company = request.form.get("company", "").strip()
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email:
            flash("Nome e e-mail são obrigatórios.", "danger")
        elif len(state) not in (0, 2):
            flash("Informe a UF com 2 letras.", "danger")
        elif User.query.filter(User.email == email, User.id != user.id).first():
            flash("Este e-mail já está sendo usado por outra conta.", "warning")
        elif new_password and len(new_password) < 6:
            flash("A nova senha deve ter pelo menos 6 caracteres.", "danger")
        elif new_password and new_password != confirm_password:
            flash("A confirmação da nova senha não coincide.", "danger")
        else:
            user.name = name
            user.email = email
            user.phone = phone or None
            user.city = city or None
            user.state = state or None
            user.profession = profession or None
            user.company = company or None

            if new_password:
                user.set_password(new_password)

            db.session.commit()
            flash("Cadastro do cliente atualizado com sucesso.", "success")
            return redirect(url_for("admin_user_detail", user_id=user.id))

    requests_list = ServiceRequest.query.filter_by(user_id=user.id).order_by(ServiceRequest.created_at.desc()).all()
    quotes = QuoteRequest.query.filter_by(user_id=user.id).order_by(QuoteRequest.created_at.desc()).all()

    return render_template(
        "admin_user_detail.html",
        user=user,
        requests_list=requests_list,
        quotes=quotes,
    )


@app.route("/admin/usuarios/<int:user_id>/bloquear", methods=["POST"])
@login_required
@admin_required
def toggle_user_block(user_id):
    user = User.query.get_or_404(user_id)
    main_admin_email = os.getenv("ADMIN_EMAIL", "admin@silasmendes.local").strip().lower()

    if user.email.lower() == main_admin_email:
        flash("Não é permitido bloquear a conta administrativa principal.", "danger")
    elif user.id == current_user.id:
        flash("Você não pode bloquear a própria conta enquanto está conectado.", "danger")
    else:
        user.is_blocked = not user.is_blocked
        db.session.commit()
        flash("Usuário desbloqueado." if not user.is_blocked else "Usuário bloqueado.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/usuarios/<int:user_id>/excluir", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    main_admin_email = os.getenv("ADMIN_EMAIL", "admin@silasmendes.local").strip().lower()

    if user.email.lower() == main_admin_email:
        flash("A conta administrativa principal não pode ser excluída.", "danger")
        return redirect(url_for("admin_users"))

    if user.id == current_user.id:
        flash("Você não pode excluir a própria conta enquanto está conectado.", "danger")
        return redirect(url_for("admin_users"))

    # Remove primeiro os registros ligados ao usuário para manter o banco consistente.
    ProfilePhoto.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    ServiceReview.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    ServiceRequest.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    quote_ids = [
        quote.id for quote in QuoteRequest.query.filter_by(user_id=user.id).all()
    ]
    if quote_ids:
        ProjectPayment.query.filter(
            ProjectPayment.quote_request_id.in_(quote_ids)
        ).delete(synchronize_session=False)

    QuoteRequest.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    deleted_name = user.name
    db.session.delete(user)
    db.session.commit()
    flash(f"Cliente {deleted_name} excluído com sucesso.", "success")
    return redirect(url_for("admin_users"))



def _table_columns(table_name):
    """Retorna nomes de colunas no SQLite ou PostgreSQL."""
    inspector = inspect(db.engine)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _datetime_sql_type():
    return "TIMESTAMP" if db.engine.dialect.name == "postgresql" else "DATETIME"


def _binary_sql_type():
    return "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"


def ensure_user_privacy_columns():
    """Mantém campos de privacidade/perfil em SQLite e PostgreSQL."""
    columns = _table_columns("user")
    additions = {
        "privacy_accepted_at": _datetime_sql_type(),
        "terms_accepted_at": _datetime_sql_type(),
        "address": "VARCHAR(180)",
        "neighborhood": "VARCHAR(120)",
        "city": "VARCHAR(100)",
        "state": "VARCHAR(2)",
        "postal_code": "VARCHAR(12)",
        "profession": "VARCHAR(120)",
        "company": "VARCHAR(150)",
        "profile_image": "VARCHAR(255)",
        "profile_image_data": _binary_sql_type(),
        "profile_image_mime": "VARCHAR(50)",
    }
    for name, sql_type in additions.items():
        if name not in columns:
            db.session.execute(
                db.text(f'ALTER TABLE "user" ADD COLUMN {name} {sql_type}')
            )
    db.session.commit()


def ensure_service_request_columns():
    """Mantém campos de solicitações em SQLite e PostgreSQL."""
    columns = _table_columns("service_request")
    additions = {
        "price_cents": "INTEGER",
        "payment_method": "VARCHAR(40)",
        "cancellation_reason": "TEXT",
        "completed_at": _datetime_sql_type(),
        "cancelled_at": _datetime_sql_type(),
    }
    for name, sql_type in additions.items():
        if name not in columns:
            db.session.execute(
                db.text(f'ALTER TABLE service_request ADD COLUMN {name} {sql_type}')
            )
    db.session.commit()


def ensure_quote_request_columns():
    """Mantém campos de orçamentos em SQLite e PostgreSQL."""
    columns = _table_columns("quote_request")
    additions = {
        "final_price_cents": "INTEGER",
        "cancellation_reason": "TEXT",
        "completed_at": _datetime_sql_type(),
        "cancelled_at": _datetime_sql_type(),
    }
    for name, sql_type in additions.items():
        if name not in columns:
            db.session.execute(
                db.text(f'ALTER TABLE quote_request ADD COLUMN {name} {sql_type}')
            )
    db.session.commit()

def seed_service_prices():
    """Cria os preços iniciais apenas quando ainda não existem."""
    changed = False
    for service_name, price_cents in DEFAULT_SERVICE_PRICES.items():
        existing = ServicePrice.query.filter_by(service_name=service_name).first()
        if not existing:
            db.session.add(
                ServicePrice(
                    service_name=service_name,
                    price_cents=price_cents,
                )
            )
            changed = True

    if changed:
        db.session.commit()


def create_admin():
    email = os.getenv("ADMIN_EMAIL", "admin@silasmendes.local").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not password:
        if os.getenv("RENDER"):
            raise RuntimeError("ADMIN_PASSWORD não foi configurada no Render.")
        password = "20262026"
    name = os.getenv("ADMIN_NAME", "Silas Mendes")
    admin = User.query.filter_by(email=email).first()

    if not admin:
        admin = User(name=name, email=email, is_admin=True)
        db.session.add(admin)
        print(f"Administrador criado: {email}")

    # Mantém a conta administrativa sincronizada com o .env.
    admin.name = name
    admin.is_admin = True
    admin.is_blocked = False
    admin.set_password(password)
    db.session.commit()


with app.app_context():
    db.create_all()
    ensure_user_privacy_columns()
    ensure_service_request_columns()
    ensure_quote_request_columns()
    seed_service_prices()
    create_admin()


if __name__ == "__main__":
    app.run(debug=True)
