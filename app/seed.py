"""Insert demo seed patients. Run: uv run python -m app.seed"""
from datetime import date

from app.database import Base, SessionLocal, engine
from app.models import Patient

SEED_PATIENTS = [
    dict(
        first_name="Jane", last_name="Doe", date_of_birth=date(1985, 3, 12),
        sex="Female", phone_number="4155550123", email="jane.doe@example.com",
        address_line_1="123 Market Street", address_line_2="Apt 4B",
        city="San Francisco", state="CA", zip_code="94103",
        insurance_provider="Blue Cross", insurance_member_id="BC123456789",
        preferred_language="English",
        emergency_contact_name="John Doe", emergency_contact_phone="4155550124",
    ),
    dict(
        first_name="Carlos", last_name="Rivera", date_of_birth=date(1978, 11, 2),
        sex="Male", phone_number="7135550188",
        address_line_1="88 Bayou Lane", city="Houston", state="TX",
        zip_code="77002", preferred_language="Spanish",
    ),
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for data in SEED_PATIENTS:
            exists = (
                db.query(Patient)
                .filter(Patient.phone_number == data["phone_number"])
                .first()
            )
            if exists:
                print(f"Skipping {data['first_name']} {data['last_name']} (already seeded)")
                continue
            db.add(Patient(**data))
            print(f"Seeded {data['first_name']} {data['last_name']}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
