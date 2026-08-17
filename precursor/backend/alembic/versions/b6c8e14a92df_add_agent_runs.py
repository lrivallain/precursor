"""split agent execution state into agent_runs

``agent_sessions`` was two things at once: the *definition* of an agent and the
state of whatever was currently driving it. That held only while an agent could
have at most one execution in flight — the moment two workflows point a step at
the same agent, they overwrite each other's status, inherit each other's live SDK
session, wipe each other's artifacts and mis-attribute each other's tokens.

This revision introduces ``agent_runs`` (one row per execution) and rewires the
execution-scoped children to it:

* ``agent_sessions.public_id`` — the durable address, backfilled from the old
  ``copilot_session_id`` so ``/agents/{uuid}`` deep links keep resolving.
* ``agent_sessions.copilot_session_id`` — dropped; the SDK handle is per-run now.
* ``agent_sessions.current_run_id`` — pointer to the newest run (circular FK, so
  it is added after ``agent_runs`` exists and created with ``use_alter``).
* ``agent_artifacts.agent_run_id`` / ``agent_events.agent_run_id`` /
  ``workflow_run_steps.agent_run_id``.

Every existing agent gets one synthetic run carrying its current execution state,
so history and in-flight sessions survive the upgrade.

Revision ID: b6c8e14a92df
Revises: a7f3c91b5d20
Create Date: 2026-08-21 10:15:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6c8e14a92df"
down_revision: str | None = "a7f3c91b5d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table: str) -> bool:
    return table in set(sa.inspect(op.get_bind()).get_table_names())


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    if not _has_table(table):
        return False
    return index in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. agent_runs ------------------------------------------------------
    # Guarded like every other DDL step here: SQLite treats DDL as
    # non-transactional, so a crash between two statements would otherwise leave
    # the revision unstamped and every later boot dying on "already exists".
    if not _has_table("agent_runs"):
        op.create_table(
            "agent_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("agent_id", sa.Integer(), nullable=False),
            sa.Column("workflow_run_id", sa.Integer(), nullable=True),
            sa.Column("workflow_run_step_id", sa.Integer(), nullable=True),
            sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("copilot_session_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("active_prompt", sa.Text(), nullable=True),
            sa.Column("blocked_question", sa.Text(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("progress_label", sa.String(length=200), nullable=True),
            sa.Column("stall_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_progress", sa.Text(), nullable=True),
            sa.Column("total_input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("model", sa.String(length=100), nullable=True),
            sa.Column("use_mcp", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("use_skills", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("use_memory", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("mcp_servers", sa.String(length=400), nullable=True),
            sa.Column("approval_policy", sa.String(length=16), nullable=True),
            sa.Column("role_id", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["agent_id"], ["agent_sessions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["workflow_run_step_id"], ["workflow_run_steps.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
    for name, cols in (
        ("ix_agent_runs_agent_id", ["agent_id"]),
        ("ix_agent_runs_workflow_run_id", ["workflow_run_id"]),
        ("ix_agent_runs_copilot_session_id", ["copilot_session_id"]),
        ("ix_agent_runs_status", ["status"]),
        ("ix_agent_runs_agent_created", ["agent_id", "created_at"]),
    ):
        if not _has_index("agent_runs", name):
            op.create_index(name, "agent_runs", cols)

    # --- 2. agent_sessions.public_id ----------------------------------------
    # Added nullable, backfilled, *then* indexed unique: an existing install has
    # rows, and a NOT NULL UNIQUE column can't be added to them in one shot.
    if not _has_column("agent_sessions", "public_id"):
        op.add_column("agent_sessions", sa.Column("public_id", sa.String(length=64), nullable=True))
        # The old SDK handle *was* the public address, so carrying it over keeps
        # every existing deep link, `/agent <uuid>` nudge and transfer lookup
        # working.
        op.execute(
            sa.text(
                "UPDATE agent_sessions SET public_id = copilot_session_id "
                "WHERE copilot_session_id IS NOT NULL"
            )
        )
        # Legacy rows that never connected have no handle to inherit; mint one so
        # the column can go NOT NULL.
        rows = bind.execute(
            sa.text("SELECT id FROM agent_sessions WHERE public_id IS NULL")
        ).fetchall()
        for (agent_id,) in rows:
            bind.execute(
                sa.text("UPDATE agent_sessions SET public_id = :pid WHERE id = :aid"),
                {"pid": str(uuid.uuid4()), "aid": agent_id},
            )
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.alter_column("public_id", existing_type=sa.String(length=64), nullable=False)
    if not _has_index("agent_sessions", "ix_agent_sessions_public_id"):
        op.create_index("ix_agent_sessions_public_id", "agent_sessions", ["public_id"], unique=True)

    # --- 3. agent_sessions.current_run_id -----------------------------------
    # ``use_alter`` because agent_sessions <-> agent_runs is a cycle; on SQLite
    # batch mode rebuilds the table with the constraint inline anyway.
    if not _has_column("agent_sessions", "current_run_id"):
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.add_column(sa.Column("current_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_agent_current_run",
                "agent_runs",
                ["current_run_id"],
                ["id"],
                ondelete="SET NULL",
                use_alter=True,
            )

    # --- 4. run-scoping columns on the children -----------------------------
    if not _has_column("agent_artifacts", "agent_run_id"):
        with op.batch_alter_table("agent_artifacts") as batch_op:
            batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_agent_artifacts_run", "agent_runs", ["agent_run_id"], ["id"], ondelete="CASCADE"
            )
    if not _has_index("agent_artifacts", "ix_agent_artifacts_agent_run_id"):
        op.create_index("ix_agent_artifacts_agent_run_id", "agent_artifacts", ["agent_run_id"])

    if not _has_column("agent_events", "agent_run_id"):
        with op.batch_alter_table("agent_events") as batch_op:
            batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_agent_events_run", "agent_runs", ["agent_run_id"], ["id"], ondelete="CASCADE"
            )
    if not _has_index("agent_events", "ix_agent_events_agent_run_id"):
        op.create_index("ix_agent_events_agent_run_id", "agent_events", ["agent_run_id"])

    if not _has_column("workflow_run_steps", "agent_run_id"):
        with op.batch_alter_table("workflow_run_steps") as batch_op:
            batch_op.add_column(sa.Column("agent_run_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_workflow_run_steps_agent_run",
                "agent_runs",
                ["agent_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if not _has_index("workflow_run_steps", "ix_workflow_run_steps_agent_run_id"):
        op.create_index(
            "ix_workflow_run_steps_agent_run_id", "workflow_run_steps", ["agent_run_id"]
        )

    # --- 5. backfill one synthetic run per agent ----------------------------
    # Guarded by "does this agent already have a run" rather than a global count,
    # so a partially-applied upgrade resumes instead of double-inserting.
    if _has_column("agent_sessions", "copilot_session_id"):
        agents = (
            bind.execute(
                sa.text(
                    "SELECT s.id, s.copilot_session_id, s.status, s.active_prompt, "
                    "s.blocked_question, s.result_summary, s.error, s.step_count, "
                    "s.progress, s.progress_label, s.total_input_tokens, "
                    "s.total_output_tokens, s.model, s.use_mcp, s.use_skills, "
                    "s.use_memory, s.mcp_servers, s.approval_policy, s.role_id, "
                    "s.created_at, s.finished_at, s.last_activity_at "
                    "FROM agent_sessions s "
                    "WHERE NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.agent_id = s.id)"
                )
            )
            .mappings()
            .all()
        )
        for row in agents:
            bind.execute(
                sa.text(
                    "INSERT INTO agent_runs ("
                    "  agent_id, trigger, copilot_session_id, status, active_prompt,"
                    "  blocked_question, result_summary, error, step_count, progress,"
                    "  progress_label, stall_count, total_input_tokens, total_output_tokens,"
                    "  model, use_mcp, use_skills, use_memory, mcp_servers, approval_policy,"
                    "  role_id, started_at, finished_at, last_activity_at, created_at, updated_at"
                    ") VALUES ("
                    "  :agent_id, 'manual', :copilot_session_id, :status, :active_prompt,"
                    "  :blocked_question, :result_summary, :error, :step_count, :progress,"
                    "  :progress_label, 0, :total_input_tokens, :total_output_tokens,"
                    "  :model, :use_mcp, :use_skills, :use_memory, :mcp_servers, :approval_policy,"
                    "  :role_id, :started_at, :finished_at, :last_activity_at, :created_at,"
                    "  :created_at"
                    ")"
                ),
                {
                    "agent_id": row["id"],
                    "copilot_session_id": row["copilot_session_id"],
                    "status": row["status"],
                    "active_prompt": row["active_prompt"],
                    "blocked_question": row["blocked_question"],
                    "result_summary": row["result_summary"],
                    "error": row["error"],
                    "step_count": row["step_count"],
                    "progress": row["progress"],
                    "progress_label": row["progress_label"],
                    "total_input_tokens": row["total_input_tokens"],
                    "total_output_tokens": row["total_output_tokens"],
                    "model": row["model"],
                    "use_mcp": row["use_mcp"],
                    "use_skills": row["use_skills"],
                    "use_memory": row["use_memory"],
                    "mcp_servers": row["mcp_servers"],
                    "approval_policy": row["approval_policy"],
                    "role_id": row["role_id"],
                    "started_at": row["created_at"],
                    "finished_at": row["finished_at"],
                    "last_activity_at": row["last_activity_at"],
                    "created_at": row["created_at"],
                },
            )
            run_id = bind.execute(
                sa.text("SELECT id FROM agent_runs WHERE agent_id = :aid ORDER BY id DESC LIMIT 1"),
                {"aid": row["id"]},
            ).scalar()
            bind.execute(
                sa.text("UPDATE agent_sessions SET current_run_id = :rid WHERE id = :aid"),
                {"rid": run_id, "aid": row["id"]},
            )
            # Everything this agent produced belongs to its one prior execution.
            bind.execute(
                sa.text(
                    "UPDATE agent_artifacts SET agent_run_id = :rid "
                    "WHERE agent_id = :aid AND agent_run_id IS NULL"
                ),
                {"rid": run_id, "aid": row["id"]},
            )
            bind.execute(
                sa.text(
                    "UPDATE agent_events SET agent_run_id = :rid "
                    "WHERE agent_session_id = :aid AND agent_run_id IS NULL"
                ),
                {"rid": run_id, "aid": row["id"]},
            )
            bind.execute(
                sa.text(
                    "UPDATE workflow_run_steps SET agent_run_id = :rid "
                    "WHERE agent_id = :aid AND agent_run_id IS NULL"
                ),
                {"rid": run_id, "aid": row["id"]},
            )

        # --- 6. drop the old handle -----------------------------------------
        # Drop its unique index first: SQLite batch mode rebuilds the table and
        # replays every index it reflected, so leaving this one in place would
        # have it re-created against a column that no longer exists.
        if _has_index("agent_sessions", "ix_agent_sessions_copilot_session_id"):
            op.drop_index("ix_agent_sessions_copilot_session_id", table_name="agent_sessions")
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.drop_column("copilot_session_id")


def downgrade() -> None:
    bind = op.get_bind()

    # Restore the SDK handle from the agent's newest run, so a rolled-back
    # install can still resume its live sessions.
    if not _has_column("agent_sessions", "copilot_session_id"):
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.add_column(
                sa.Column("copilot_session_id", sa.String(length=128), nullable=True)
            )
        if _has_table("agent_runs"):
            bind.execute(
                sa.text(
                    "UPDATE agent_sessions SET copilot_session_id = ("
                    "  SELECT r.copilot_session_id FROM agent_runs r "
                    "  WHERE r.agent_id = agent_sessions.id "
                    "  ORDER BY r.id DESC LIMIT 1"
                    ")"
                )
            )
        # Fall back to the public address for agents that never ran, matching the
        # old "minted at row creation" behaviour.
        bind.execute(
            sa.text(
                "UPDATE agent_sessions SET copilot_session_id = public_id "
                "WHERE copilot_session_id IS NULL"
            )
        )
        if not _has_index("agent_sessions", "ix_agent_sessions_copilot_session_id"):
            op.create_index(
                "ix_agent_sessions_copilot_session_id",
                "agent_sessions",
                ["copilot_session_id"],
                unique=True,
            )

    for table, index, column, fk in (
        (
            "workflow_run_steps",
            "ix_workflow_run_steps_agent_run_id",
            "agent_run_id",
            "fk_workflow_run_steps_agent_run",
        ),
        ("agent_events", "ix_agent_events_agent_run_id", "agent_run_id", "fk_agent_events_run"),
        (
            "agent_artifacts",
            "ix_agent_artifacts_agent_run_id",
            "agent_run_id",
            "fk_agent_artifacts_run",
        ),
    ):
        if _has_index(table, index):
            op.drop_index(index, table_name=table)
        if _has_column(table, column):
            with op.batch_alter_table(table) as batch_op:
                batch_op.drop_constraint(fk, type_="foreignkey")
                batch_op.drop_column(column)

    if _has_column("agent_sessions", "current_run_id"):
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.drop_constraint("fk_agent_current_run", type_="foreignkey")
            batch_op.drop_column("current_run_id")

    if _has_index("agent_sessions", "ix_agent_sessions_public_id"):
        op.drop_index("ix_agent_sessions_public_id", table_name="agent_sessions")
    if _has_column("agent_sessions", "public_id"):
        with op.batch_alter_table("agent_sessions") as batch_op:
            batch_op.drop_column("public_id")

    for name in (
        "ix_agent_runs_agent_created",
        "ix_agent_runs_status",
        "ix_agent_runs_copilot_session_id",
        "ix_agent_runs_workflow_run_id",
        "ix_agent_runs_agent_id",
    ):
        if _has_index("agent_runs", name):
            op.drop_index(name, table_name="agent_runs")
    if _has_table("agent_runs"):
        op.drop_table("agent_runs")
