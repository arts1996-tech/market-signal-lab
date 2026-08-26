from app.database.session import SessionLocal
from app.services.theme_definition_service import seed_theme_definitions


def main() -> None:
    with SessionLocal() as session:
        result = seed_theme_definitions(session)
        session.commit()
    print(result)


if __name__ == "__main__":
    main()
