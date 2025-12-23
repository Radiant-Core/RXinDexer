Place migration scripts here. Each migration file should be named with the format:
<revision_id>_<description>.py

To create an initial migration, run:

alembic revision --autogenerate -m "Initial migration"

Then apply it with:

alembic upgrade head
