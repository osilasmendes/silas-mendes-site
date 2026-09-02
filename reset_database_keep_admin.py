from pathlib import Path
from datetime import datetime
import shutil
import sys

from app import app, db, User


def main():
    with app.app_context():
        # Localiza a conta administrativa principal.
        admin = (
            User.query.filter_by(is_admin=True)
            .order_by(User.id.asc())
            .first()
        )

        if not admin:
            print("ERRO: nenhuma conta administrativa foi encontrada.")
            print("A limpeza foi cancelada para evitar perda do acesso ao site.")
            sys.exit(1)

        # Preserva os dados necessários para manter o mesmo login do ADM.
        admin_data = {
            "name": admin.name,
            "email": admin.email,
            "phone": admin.phone,
            "password_hash": admin.password_hash,
            "is_admin": True,
            "is_blocked": False,
            "created_at": admin.created_at,
            "privacy_accepted_at": admin.privacy_accepted_at,
            "terms_accepted_at": admin.terms_accepted_at,
            "city": admin.city,
            "state": admin.state,
            "profession": admin.profession,
            "company": admin.company,
        }

        # Descobre o arquivo SQLite real usado pelo Flask.
        database_path = Path(db.engine.url.database).resolve()

        # Fecha a sessão antes do backup/recriação.
        db.session.remove()

        # Backup automático do banco atual.
        backup_dir = database_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"silas_mendes_antes_da_limpeza_{timestamp}.db"

        if database_path.exists():
            shutil.copy2(database_path, backup_path)
            print(f"Backup criado em: {backup_path}")
        else:
            print("Aviso: arquivo físico do banco não foi encontrado para backup.")

        print()
        print("LIMPANDO BANCO DE DADOS...")
        print("- clientes")
        print("- solicitações de serviços")
        print("- orçamentos de sites e sistemas")
        print("- pagamentos de projetos")
        print("- avaliações e depoimentos")
        print("- históricos e cancelamentos")
        print()

        # Remove todas as tabelas e recria a estrutura vazia.
        db.drop_all()
        db.create_all()

        # Recria somente a conta administrativa, mantendo a senha atual.
        clean_admin = User(
            id=1,
            name=admin_data["name"],
            email=admin_data["email"],
            phone=admin_data["phone"],
            password_hash=admin_data["password_hash"],
            is_admin=True,
            is_blocked=False,
            created_at=admin_data["created_at"] or datetime.utcnow(),
            privacy_accepted_at=admin_data["privacy_accepted_at"],
            terms_accepted_at=admin_data["terms_accepted_at"],
            city=admin_data["city"],
            state=admin_data["state"],
            profession=admin_data["profession"],
            company=admin_data["company"],
        )

        db.session.add(clean_admin)
        db.session.commit()

        print("=" * 58)
        print("BANCO LIMPO COM SUCESSO")
        print("=" * 58)
        print(f"Administrador mantido: {clean_admin.email}")
        print("ID do administrador: 1")
        print()
        print("O site agora está sem clientes, pedidos, avaliações ou vendas.")
        print("O próximo cadastro de cliente começará normalmente do zero.")
        print()
        print(f"Backup de segurança: {backup_path}")


if __name__ == "__main__":
    main()
