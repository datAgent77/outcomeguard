"""p06 auth sessions, manual reviews, policy decisions, capture approvals

Revision ID: c3be1fa32c64
Revises: 289e9013df66
Create Date: 2026-10-15 17:04:02.757124

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3be1fa32c64'
down_revision: Union[str, Sequence[str], None] = '289e9013df66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GOVERNANCE_SQL = """
CREATE TRIGGER og_manual_review_decisions_append_only
  BEFORE UPDATE OR DELETE ON manual_review_decisions
  FOR EACH ROW EXECUTE FUNCTION og_append_only();
CREATE TRIGGER og_policy_decisions_append_only
  BEFORE UPDATE OR DELETE ON policy_decisions
  FOR EACH ROW EXECUTE FUNCTION og_append_only();

-- A manual-review decision must target a manual_review_required result of a completed run
-- of the same agreement version.
CREATE FUNCTION og_check_manual_review() RETURNS trigger AS $$
DECLARE ok boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM verification_runs r
      JOIN verification_results vr ON vr.run_id = r.id
      JOIN acceptance_criteria c ON c.id = vr.criterion_id
     WHERE r.id = NEW.verification_run_id
       AND r.status = 'completed'
       AND r.agreement_version_id = NEW.agreement_version_id
       AND r.agreement_content_hash = NEW.agreement_content_hash
       AND vr.criterion_id = NEW.criterion_id
       AND vr.outcome = 'manual_review_required'
       AND c.agreement_version_id = NEW.agreement_version_id
  ) INTO ok;
  IF NOT ok THEN
    RAISE EXCEPTION 'manual review decision does not match a pending manual criterion of this run/version'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_manual_review_decisions_check BEFORE INSERT ON manual_review_decisions
  FOR EACH ROW EXECUTE FUNCTION og_check_manual_review();

-- Capture approvals: append-only except a single consumption stamp.
CREATE FUNCTION og_protect_capture_approval() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'capture approvals cannot be deleted'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.consumed_by_capture_id IS NOT NULL OR NEW.consumed_by_capture_id IS NULL
     OR (NEW.payment_authorization_id, NEW.policy_decision_id, NEW.inputs_sha256,
         NEW.approver_user_id, NEW.approver_role, NEW.organization_id, NEW.approved_at)
        IS DISTINCT FROM
        (OLD.payment_authorization_id, OLD.policy_decision_id, OLD.inputs_sha256,
         OLD.approver_user_id, OLD.approver_role, OLD.organization_id, OLD.approved_at) THEN
    RAISE EXCEPTION 'capture approval % can only be consumed once', OLD.id
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_capture_approvals_protect BEFORE UPDATE OR DELETE ON capture_approvals
  FOR EACH ROW EXECUTE FUNCTION og_protect_capture_approval();

-- Database-level policy reference enforcement: a capture of an agreement-bound payment
-- must reference a CAPTURE_APPROVED policy decision and a capture approval for the same
-- authorization and the same policy inputs hash, and the run the policy evaluated.
CREATE FUNCTION og_require_capture_policy() RETURNS trigger AS $$
DECLARE
  bound text;
  pd record;
  ap record;
BEGIN
  SELECT o.agreement_version_id INTO bound
    FROM payment_authorizations a JOIN payment_orders o ON o.id = a.payment_order_id
   WHERE a.id = NEW.payment_authorization_id;
  IF bound IS NULL THEN
    RETURN NEW;  -- P02 sandbox-proof orders are not agreement-bound
  END IF;
  IF NEW.status NOT IN ('requested', 'unknown', 'pending', 'completed') THEN
    RETURN NEW;
  END IF;
  IF NEW.policy_decision_id IS NULL OR NEW.approval_id IS NULL OR NEW.verification_run_id IS NULL THEN
    RAISE EXCEPTION 'agreement-bound capture requires policy decision, capture approval and verification run'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  SELECT * INTO pd FROM policy_decisions WHERE id = NEW.policy_decision_id;
  SELECT * INTO ap FROM capture_approvals WHERE id = NEW.approval_id;
  IF pd.decision <> 'capture_approved'
     OR pd.payment_authorization_id <> NEW.payment_authorization_id
     OR pd.verification_run_id IS DISTINCT FROM NEW.verification_run_id
     OR pd.agreement_version_id IS DISTINCT FROM bound
     OR ap.payment_authorization_id <> NEW.payment_authorization_id
     OR ap.inputs_sha256 <> pd.inputs_sha256 THEN
    RAISE EXCEPTION 'capture policy references do not match this authorization'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_payment_captures_policy BEFORE INSERT OR UPDATE ON payment_captures
  FOR EACH ROW EXECUTE FUNCTION og_require_capture_policy();
"""

DROP_GOVERNANCE_SQL = """
DROP TRIGGER IF EXISTS og_payment_captures_policy ON payment_captures;
DROP FUNCTION IF EXISTS og_require_capture_policy();
DROP TRIGGER IF EXISTS og_capture_approvals_protect ON capture_approvals;
DROP FUNCTION IF EXISTS og_protect_capture_approval();
DROP TRIGGER IF EXISTS og_manual_review_decisions_check ON manual_review_decisions;
DROP FUNCTION IF EXISTS og_check_manual_review();
DROP TRIGGER IF EXISTS og_policy_decisions_append_only ON policy_decisions;
DROP TRIGGER IF EXISTS og_manual_review_decisions_append_only ON manual_review_decisions;
"""


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('auth_sessions',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_sha256', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_sha256', name=op.f('uq_auth_sessions_token_sha256'))
    )
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('manual_review_decisions',
    sa.Column('agreement_version_id', sa.String(length=36), nullable=False),
    sa.Column('agreement_content_hash', sa.String(length=64), nullable=False),
    sa.Column('criterion_id', sa.String(length=36), nullable=False),
    sa.Column('verification_run_id', sa.String(length=36), nullable=False),
    sa.Column('evidence_snapshot_sha256', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.Enum('satisfied', 'not_satisfied', name='review_decision', native_enum=False, length=40), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('reviewer_user_id', sa.String(length=36), nullable=False),
    sa.Column('reviewer_role', sa.String(length=40), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['agreement_version_id'], ['agreement_versions.id'], name=op.f('fk_manual_review_decisions_agreement_version_id_agreement_versions')),
    sa.ForeignKeyConstraint(['criterion_id'], ['acceptance_criteria.id'], name=op.f('fk_manual_review_decisions_criterion_id_acceptance_criteria')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_manual_review_decisions_organization_id_organizations')),
    sa.ForeignKeyConstraint(['reviewer_user_id'], ['users.id'], name=op.f('fk_manual_review_decisions_reviewer_user_id_users')),
    sa.ForeignKeyConstraint(['verification_run_id'], ['verification_runs.id'], name=op.f('fk_manual_review_decisions_verification_run_id_verification_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_manual_review_decisions')),
    sa.UniqueConstraint('verification_run_id', 'criterion_id', name='uq_manual_review_run_criterion')
    )
    op.create_index(op.f('ix_manual_review_decisions_verification_run_id'), 'manual_review_decisions', ['verification_run_id'], unique=False)
    op.create_table('policy_decisions',
    sa.Column('payment_authorization_id', sa.String(length=36), nullable=False),
    sa.Column('agreement_version_id', sa.String(length=36), nullable=True),
    sa.Column('verification_run_id', sa.String(length=36), nullable=True),
    sa.Column('purpose', sa.String(length=20), nullable=False),
    sa.Column('decision', sa.Enum('blocked', 'needs_review', 'eligible_for_approval', 'capture_approved', 'captured', name='policy_outcome', native_enum=False, length=40), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_sha256', sa.String(length=64), nullable=False),
    sa.Column('evaluated_by', sa.String(length=200), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['agreement_version_id'], ['agreement_versions.id'], name=op.f('fk_policy_decisions_agreement_version_id_agreement_versions')),
    sa.ForeignKeyConstraint(['payment_authorization_id'], ['payment_authorizations.id'], name=op.f('fk_policy_decisions_payment_authorization_id_payment_authorizations')),
    sa.ForeignKeyConstraint(['verification_run_id'], ['verification_runs.id'], name=op.f('fk_policy_decisions_verification_run_id_verification_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_policy_decisions'))
    )
    op.create_index(op.f('ix_policy_decisions_inputs_sha256'), 'policy_decisions', ['inputs_sha256'], unique=False)
    op.create_index(op.f('ix_policy_decisions_payment_authorization_id'), 'policy_decisions', ['payment_authorization_id'], unique=False)
    op.create_table('capture_approvals',
    sa.Column('payment_authorization_id', sa.String(length=36), nullable=False),
    sa.Column('policy_decision_id', sa.String(length=36), nullable=False),
    sa.Column('inputs_sha256', sa.String(length=64), nullable=False),
    sa.Column('approver_user_id', sa.String(length=36), nullable=False),
    sa.Column('approver_role', sa.String(length=40), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('consumed_by_capture_id', sa.String(length=36), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['approver_user_id'], ['users.id'], name=op.f('fk_capture_approvals_approver_user_id_users')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_capture_approvals_organization_id_organizations')),
    sa.ForeignKeyConstraint(['payment_authorization_id'], ['payment_authorizations.id'], name=op.f('fk_capture_approvals_payment_authorization_id_payment_authorizations')),
    sa.ForeignKeyConstraint(['policy_decision_id'], ['policy_decisions.id'], name=op.f('fk_capture_approvals_policy_decision_id_policy_decisions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_capture_approvals')),
    sa.UniqueConstraint('payment_authorization_id', 'inputs_sha256', name='uq_capture_approval_auth_inputs')
    )
    op.create_index(op.f('ix_capture_approvals_payment_authorization_id'), 'capture_approvals', ['payment_authorization_id'], unique=False)
    op.drop_constraint(op.f('fk_payment_captures_approval_id_approvals'), 'payment_captures', type_='foreignkey')
    op.create_foreign_key(op.f('fk_payment_captures_policy_decision_id_policy_decisions'), 'payment_captures', 'policy_decisions', ['policy_decision_id'], ['id'])
    op.create_foreign_key(op.f('fk_payment_captures_approval_id_capture_approvals'), 'payment_captures', 'capture_approvals', ['approval_id'], ['id'])
    op.add_column('users', sa.Column('kind', sa.Enum('human', 'service', name='user_kind', native_enum=False, length=40), server_default='human', nullable=False))
    op.add_column('users', sa.Column('password_hash', sa.String(length=300), nullable=True))
    op.add_column('users', sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.execute(GOVERNANCE_SQL)
    # ### end Alembic commands ###


def downgrade() -> None:
    op.execute(DROP_GOVERNANCE_SQL)
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_logins')
    op.drop_column('users', 'password_hash')
    op.drop_column('users', 'kind')
    op.drop_constraint(op.f('fk_payment_captures_approval_id_capture_approvals'), 'payment_captures', type_='foreignkey')
    op.drop_constraint(op.f('fk_payment_captures_policy_decision_id_policy_decisions'), 'payment_captures', type_='foreignkey')
    op.create_foreign_key(op.f('fk_payment_captures_approval_id_approvals'), 'payment_captures', 'approvals', ['approval_id'], ['id'])
    op.drop_index(op.f('ix_capture_approvals_payment_authorization_id'), table_name='capture_approvals')
    op.drop_table('capture_approvals')
    op.drop_index(op.f('ix_policy_decisions_payment_authorization_id'), table_name='policy_decisions')
    op.drop_index(op.f('ix_policy_decisions_inputs_sha256'), table_name='policy_decisions')
    op.drop_table('policy_decisions')
    op.drop_index(op.f('ix_manual_review_decisions_verification_run_id'), table_name='manual_review_decisions')
    op.drop_table('manual_review_decisions')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    # ### end Alembic commands ###
