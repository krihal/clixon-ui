from alembic import context

from clixon_ui.db import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through clixon_ui.db.migrate()")
context.configure(connection=connection, target_metadata=Base.metadata)
with context.begin_transaction():
    context.run_migrations()
