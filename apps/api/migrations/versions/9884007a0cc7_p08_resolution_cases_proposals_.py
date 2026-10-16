"""p08 resolution cases proposals approvals voids refund governance

Revision ID: 9884007a0cc7
Revises: c3be1fa32c64
Create Date: 2026-10-16 12:04:27.658619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9884007a0cc7'
down_revision: Union[str, Sequence[str], None] = 'c3be1fa32c64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESOLUTION_SQL = """
-- Append-only records.
CREATE TRIGGER og_resolution_events_append_only BEFORE UPDATE OR DELETE ON resolution_events
  FOR EACH ROW EXECUTE FUNCTION og_append_only();
CREATE TRIGGER og_resolution_approvals_append_only BEFORE UPDATE OR DELETE ON resolution_approvals
  FOR EACH ROW EXECUTE FUNCTION og_append_only();
CREATE TRIGGER og_financial_policy_decisions_append_only
  BEFORE UPDATE OR DELETE ON financial_policy_decisions
  FOR EACH ROW EXECUTE FUNCTION og_append_only();

-- Cases: never deleted; what the case is about and the evidence snapshot never change.
CREATE FUNCTION og_protect_resolution_case() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'resolution_cases cannot be deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.agreement_id <> OLD.agreement_id
     OR NEW.agreement_version_id <> OLD.agreement_version_id
     OR NEW.agreement_content_hash <> OLD.agreement_content_hash
     OR NEW.payment_capture_id IS DISTINCT FROM OLD.payment_capture_id
     OR NEW.payment_authorization_id IS DISTINCT FROM OLD.payment_authorization_id
     OR NEW.buyer_organization_id <> OLD.buyer_organization_id
     OR NEW.merchant_organization_id <> OLD.merchant_organization_id
     OR NEW.opened_by_user_id <> OLD.opened_by_user_id
     OR NEW.description <> OLD.description
     OR NEW.evidence_snapshot::text <> OLD.evidence_snapshot::text
     OR NEW.evidence_snapshot_sha256 <> OLD.evidence_snapshot_sha256
     OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'resolution case facts and evidence snapshot are immutable'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.status IN ('resolved', 'closed_no_action', 'rejected', 'cancelled') THEN
    RAISE EXCEPTION 'resolution case is closed' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_resolution_cases_protect BEFORE UPDATE OR DELETE ON resolution_cases
  FOR EACH ROW EXECUTE FUNCTION og_protect_resolution_case();

-- Proposals: content immutable; only status/decided_at move, never back to open.
CREATE FUNCTION og_protect_resolution_proposal() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'resolution_proposals cannot be deleted' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.case_id <> OLD.case_id OR NEW.number <> OLD.number
     OR NEW.author_user_id <> OLD.author_user_id OR NEW.side <> OLD.side
     OR NEW.remedy <> OLD.remedy OR NEW.amount_minor IS DISTINCT FROM OLD.amount_minor
     OR NEW.currency <> OLD.currency OR NEW.rationale <> OLD.rationale
     OR NEW.evidence_refs::text <> OLD.evidence_refs::text
     OR NEW.content_sha256 <> OLD.content_sha256 OR NEW.case_revision <> OLD.case_revision
     OR NEW.created_at <> OLD.created_at THEN
    RAISE EXCEPTION 'proposal content is immutable' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF NEW.status = 'open' AND OLD.status <> 'open' THEN
    RAISE EXCEPTION 'a decided proposal cannot reopen' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF OLD.status IN ('rejected', 'superseded', 'withdrawn', 'executed') AND NEW.status <> OLD.status THEN
    RAISE EXCEPTION 'proposal is final' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_resolution_proposals_protect BEFORE UPDATE OR DELETE ON resolution_proposals
  FOR EACH ROW EXECUTE FUNCTION og_protect_resolution_proposal();

-- Approvals must quote the proposal's content hash and come from the side's organization.
CREATE FUNCTION og_check_resolution_approval() RETURNS trigger AS $$
DECLARE
  p record;
  c record;
BEGIN
  SELECT * INTO p FROM resolution_proposals WHERE id = NEW.proposal_id;
  SELECT * INTO c FROM resolution_cases WHERE id = p.case_id;
  IF p.case_id <> NEW.case_id OR p.content_sha256 <> NEW.proposal_content_sha256 THEN
    RAISE EXCEPTION 'approval does not match the proposal' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  IF (NEW.side = 'buyer' AND NEW.organization_id <> c.buyer_organization_id)
     OR (NEW.side = 'provider' AND NEW.organization_id <> c.merchant_organization_id) THEN
    RAISE EXCEPTION 'approval side does not match the organization' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_resolution_approvals_check BEFORE INSERT ON resolution_approvals
  FOR EACH ROW EXECUTE FUNCTION og_check_resolution_approval();

-- Agreement-bound refunds need an accepted, two-sided proposal and an allowed refund
-- policy decision for exactly this capture, amount and currency.
CREATE FUNCTION og_require_refund_resolution() RETURNS trigger AS $$
DECLARE
  bound text;
  p record;
  c record;
  d record;
  approvals int;
BEGIN
  SELECT o.agreement_version_id INTO bound
    FROM payment_captures cap
    JOIN payment_authorizations a ON a.id = cap.payment_authorization_id
    JOIN payment_orders o ON o.id = a.payment_order_id
   WHERE cap.id = NEW.payment_capture_id;
  IF bound IS NULL THEN
    RETURN NEW;  -- P02 sandbox-proof captures are not agreement-bound
  END IF;
  IF NEW.status = 'failed' THEN
    RETURN NEW;
  END IF;
  IF NEW.resolution_case_id IS NULL OR NEW.proposal_id IS NULL OR NEW.policy_decision_id IS NULL THEN
    RAISE EXCEPTION 'agreement-bound refund requires a resolution case, an approved proposal and a policy decision'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  SELECT * INTO p FROM resolution_proposals WHERE id = NEW.proposal_id;
  SELECT * INTO c FROM resolution_cases WHERE id = NEW.resolution_case_id;
  SELECT * INTO d FROM financial_policy_decisions WHERE id = NEW.policy_decision_id;
  SELECT count(*) INTO approvals FROM resolution_approvals
   WHERE proposal_id = p.id AND choice = 'approve' AND proposal_content_sha256 = p.content_sha256;
  IF p.case_id <> c.id
     OR c.payment_capture_id IS DISTINCT FROM NEW.payment_capture_id
     OR p.status NOT IN ('accepted', 'executed')
     OR p.remedy NOT IN ('partial_refund', 'full_refund')
     OR p.amount_minor IS DISTINCT FROM NEW.amount_minor
     OR p.currency <> NEW.currency
     OR approvals < 2
     OR d.purpose <> 'refund' OR d.decision <> 'allowed'
     OR d.proposal_id IS DISTINCT FROM p.id
     OR d.payment_capture_id IS DISTINCT FROM NEW.payment_capture_id THEN
    RAISE EXCEPTION 'refund governance references do not match'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_payment_refunds_resolution BEFORE INSERT OR UPDATE ON payment_refunds
  FOR EACH ROW EXECUTE FUNCTION og_require_refund_resolution();

-- Governed voids need an allowed void decision for the same authorization.
CREATE FUNCTION og_require_void_policy() RETURNS trigger AS $$
DECLARE
  d record;
BEGIN
  SELECT * INTO d FROM financial_policy_decisions WHERE id = NEW.policy_decision_id;
  IF d.purpose <> 'void' OR d.decision <> 'allowed'
     OR d.payment_authorization_id IS DISTINCT FROM NEW.payment_authorization_id THEN
    RAISE EXCEPTION 'void policy reference does not match' USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER og_authorization_voids_policy BEFORE INSERT OR UPDATE ON authorization_voids
  FOR EACH ROW EXECUTE FUNCTION og_require_void_policy();
CREATE TRIGGER og_authorization_voids_no_delete BEFORE DELETE ON authorization_voids
  FOR EACH ROW EXECUTE FUNCTION og_append_only();
"""

DROP_RESOLUTION_SQL = """
DROP TRIGGER IF EXISTS og_authorization_voids_no_delete ON authorization_voids;
DROP TRIGGER IF EXISTS og_authorization_voids_policy ON authorization_voids;
DROP FUNCTION IF EXISTS og_require_void_policy();
DROP TRIGGER IF EXISTS og_payment_refunds_resolution ON payment_refunds;
DROP FUNCTION IF EXISTS og_require_refund_resolution();
DROP TRIGGER IF EXISTS og_resolution_approvals_check ON resolution_approvals;
DROP FUNCTION IF EXISTS og_check_resolution_approval();
DROP TRIGGER IF EXISTS og_resolution_proposals_protect ON resolution_proposals;
DROP FUNCTION IF EXISTS og_protect_resolution_proposal();
DROP TRIGGER IF EXISTS og_resolution_cases_protect ON resolution_cases;
DROP FUNCTION IF EXISTS og_protect_resolution_case();
DROP TRIGGER IF EXISTS og_financial_policy_decisions_append_only ON financial_policy_decisions;
DROP TRIGGER IF EXISTS og_resolution_approvals_append_only ON resolution_approvals;
DROP TRIGGER IF EXISTS og_resolution_events_append_only ON resolution_events;
"""


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('resolution_cases',
    sa.Column('agreement_id', sa.String(length=36), nullable=False),
    sa.Column('agreement_version_id', sa.String(length=36), nullable=False),
    sa.Column('agreement_content_hash', sa.String(length=64), nullable=False),
    sa.Column('payment_authorization_id', sa.String(length=36), nullable=True),
    sa.Column('payment_capture_id', sa.String(length=36), nullable=True),
    sa.Column('buyer_organization_id', sa.String(length=36), nullable=False),
    sa.Column('merchant_organization_id', sa.String(length=36), nullable=False),
    sa.Column('opened_by_user_id', sa.String(length=36), nullable=False),
    sa.Column('reason_category', sa.Enum('criterion_not_met', 'defect_after_acceptance', 'incomplete_delivery', 'late_delivery', 'cancel_before_capture', 'other', name='resolution_reason', native_enum=False, length=40), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('requested_remedy', sa.Enum('rework', 'new_verification', 'partial_refund', 'full_refund', 'cancel_authorization', 'other', 'no_remedy', name='resolution_remedy', native_enum=False, length=40), nullable=False),
    sa.Column('requested_amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('evidence_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_snapshot_sha256', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('open', 'under_review', 'proposed', 'agreed', 'executing', 'action_required', 'resolved', 'closed_no_action', 'rejected', 'cancelled', name='resolution_status', native_enum=False, length=40), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('closure_reason', sa.Text(), nullable=True),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['agreement_id'], ['agreements.id'], name=op.f('fk_resolution_cases_agreement_id_agreements')),
    sa.ForeignKeyConstraint(['agreement_version_id'], ['agreement_versions.id'], name=op.f('fk_resolution_cases_agreement_version_id_agreement_versions')),
    sa.ForeignKeyConstraint(['buyer_organization_id'], ['organizations.id'], name=op.f('fk_resolution_cases_buyer_organization_id_organizations')),
    sa.ForeignKeyConstraint(['merchant_organization_id'], ['organizations.id'], name=op.f('fk_resolution_cases_merchant_organization_id_organizations')),
    sa.ForeignKeyConstraint(['opened_by_user_id'], ['users.id'], name=op.f('fk_resolution_cases_opened_by_user_id_users')),
    sa.ForeignKeyConstraint(['payment_authorization_id'], ['payment_authorizations.id'], name=op.f('fk_resolution_cases_payment_authorization_id_payment_authorizations')),
    sa.ForeignKeyConstraint(['payment_capture_id'], ['payment_captures.id'], name=op.f('fk_resolution_cases_payment_capture_id_payment_captures')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resolution_cases'))
    )
    op.create_index(op.f('ix_resolution_cases_agreement_id'), 'resolution_cases', ['agreement_id'], unique=False)
    op.create_index('uq_resolution_cases_live_capture', 'resolution_cases', ['payment_capture_id'], unique=True, postgresql_where=sa.text("payment_capture_id IS NOT NULL AND status NOT IN ('resolved','closed_no_action','rejected','cancelled')"))
    op.create_table('resolution_events',
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('author_user_id', sa.String(length=36), nullable=True),
    sa.Column('side', sa.String(length=20), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], name=op.f('fk_resolution_events_author_user_id_users')),
    sa.ForeignKeyConstraint(['case_id'], ['resolution_cases.id'], name=op.f('fk_resolution_events_case_id_resolution_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resolution_events'))
    )
    op.create_index(op.f('ix_resolution_events_case_id'), 'resolution_events', ['case_id'], unique=False)
    op.create_table('resolution_proposals',
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('author_user_id', sa.String(length=36), nullable=False),
    sa.Column('side', sa.Enum('buyer', 'provider', name='resolution_side', native_enum=False, length=40), nullable=False),
    sa.Column('remedy', sa.Enum('rework', 'new_verification', 'partial_refund', 'full_refund', 'cancel_authorization', 'other', 'no_remedy', name='resolution_remedy', native_enum=False, length=40), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('evidence_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('case_revision', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('open', 'accepted', 'rejected', 'superseded', 'withdrawn', 'executed', name='resolution_proposal_status', native_enum=False, length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], name=op.f('fk_resolution_proposals_author_user_id_users')),
    sa.ForeignKeyConstraint(['case_id'], ['resolution_cases.id'], name=op.f('fk_resolution_proposals_case_id_resolution_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resolution_proposals')),
    sa.UniqueConstraint('case_id', 'number', name=op.f('uq_resolution_proposals_case_id'))
    )
    op.create_index(op.f('ix_resolution_proposals_case_id'), 'resolution_proposals', ['case_id'], unique=False)
    op.create_table('financial_policy_decisions',
    sa.Column('purpose', sa.Enum('refund', 'void', name='financial_purpose', native_enum=False, length=40), nullable=False),
    sa.Column('decision', sa.Enum('allowed', 'blocked', name='financial_decision', native_enum=False, length=40), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_sha256', sa.String(length=64), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=True),
    sa.Column('proposal_id', sa.String(length=36), nullable=True),
    sa.Column('payment_authorization_id', sa.String(length=36), nullable=True),
    sa.Column('payment_capture_id', sa.String(length=36), nullable=True),
    sa.Column('evaluated_by', sa.String(length=200), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['resolution_cases.id'], name=op.f('fk_financial_policy_decisions_case_id_resolution_cases')),
    sa.ForeignKeyConstraint(['payment_authorization_id'], ['payment_authorizations.id'], name=op.f('fk_financial_policy_decisions_payment_authorization_id_payment_authorizations')),
    sa.ForeignKeyConstraint(['payment_capture_id'], ['payment_captures.id'], name=op.f('fk_financial_policy_decisions_payment_capture_id_payment_captures')),
    sa.ForeignKeyConstraint(['proposal_id'], ['resolution_proposals.id'], name=op.f('fk_financial_policy_decisions_proposal_id_resolution_proposals')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_financial_policy_decisions'))
    )
    op.create_index(op.f('ix_financial_policy_decisions_inputs_sha256'), 'financial_policy_decisions', ['inputs_sha256'], unique=False)
    op.create_table('resolution_approvals',
    sa.Column('proposal_id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('side', sa.Enum('buyer', 'provider', name='resolution_side', native_enum=False, length=40), nullable=False),
    sa.Column('choice', sa.Enum('approve', 'reject', name='approval_choice', native_enum=False, length=40), nullable=False),
    sa.Column('approver_user_id', sa.String(length=36), nullable=False),
    sa.Column('approver_role', sa.String(length=40), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('proposal_content_sha256', sa.String(length=64), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.ForeignKeyConstraint(['approver_user_id'], ['users.id'], name=op.f('fk_resolution_approvals_approver_user_id_users')),
    sa.ForeignKeyConstraint(['case_id'], ['resolution_cases.id'], name=op.f('fk_resolution_approvals_case_id_resolution_cases')),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_resolution_approvals_organization_id_organizations')),
    sa.ForeignKeyConstraint(['proposal_id'], ['resolution_proposals.id'], name=op.f('fk_resolution_approvals_proposal_id_resolution_proposals')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resolution_approvals')),
    sa.UniqueConstraint('proposal_id', 'side', name=op.f('uq_resolution_approvals_proposal_id'))
    )
    op.create_index(op.f('ix_resolution_approvals_proposal_id'), 'resolution_approvals', ['proposal_id'], unique=False)
    op.create_table('authorization_voids',
    sa.Column('payment_authorization_id', sa.String(length=36), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('requested', 'unknown', 'completed', 'failed', name='void_status', native_enum=False, length=40), nullable=False),
    sa.Column('requested_by', sa.String(length=200), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('policy_decision_id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=True),
    sa.Column('proposal_id', sa.String(length=36), nullable=True),
    sa.Column('paypal_status', sa.String(length=40), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['resolution_cases.id'], name=op.f('fk_authorization_voids_case_id_resolution_cases')),
    sa.ForeignKeyConstraint(['payment_authorization_id'], ['payment_authorizations.id'], name=op.f('fk_authorization_voids_payment_authorization_id_payment_authorizations')),
    sa.ForeignKeyConstraint(['policy_decision_id'], ['financial_policy_decisions.id'], name=op.f('fk_authorization_voids_policy_decision_id_financial_policy_decisions')),
    sa.ForeignKeyConstraint(['proposal_id'], ['resolution_proposals.id'], name=op.f('fk_authorization_voids_proposal_id_resolution_proposals')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_authorization_voids')),
    sa.UniqueConstraint('payment_authorization_id', name=op.f('uq_authorization_voids_payment_authorization_id')),
    sa.UniqueConstraint('request_id', name=op.f('uq_authorization_voids_request_id'))
    )
    op.add_column('payment_refunds', sa.Column('resolution_case_id', sa.String(length=36), nullable=True))
    op.add_column('payment_refunds', sa.Column('proposal_id', sa.String(length=36), nullable=True))
    op.add_column('payment_refunds', sa.Column('policy_decision_id', sa.String(length=36), nullable=True))
    op.create_index('uq_payment_refunds_live_proposal', 'payment_refunds', ['proposal_id'], unique=True, postgresql_where=sa.text("proposal_id IS NOT NULL AND status <> 'failed'"))
    op.create_foreign_key(op.f('fk_payment_refunds_proposal_id_resolution_proposals'), 'payment_refunds', 'resolution_proposals', ['proposal_id'], ['id'])
    op.create_foreign_key(op.f('fk_payment_refunds_policy_decision_id_financial_policy_decisions'), 'payment_refunds', 'financial_policy_decisions', ['policy_decision_id'], ['id'])
    op.create_foreign_key(op.f('fk_payment_refunds_resolution_case_id_resolution_cases'), 'payment_refunds', 'resolution_cases', ['resolution_case_id'], ['id'])
    # ### end Alembic commands ###
    op.execute(RESOLUTION_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_RESOLUTION_SQL)
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(op.f('fk_payment_refunds_resolution_case_id_resolution_cases'), 'payment_refunds', type_='foreignkey')
    op.drop_constraint(op.f('fk_payment_refunds_policy_decision_id_financial_policy_decisions'), 'payment_refunds', type_='foreignkey')
    op.drop_constraint(op.f('fk_payment_refunds_proposal_id_resolution_proposals'), 'payment_refunds', type_='foreignkey')
    op.drop_index('uq_payment_refunds_live_proposal', table_name='payment_refunds', postgresql_where=sa.text("proposal_id IS NOT NULL AND status <> 'failed'"))
    op.drop_column('payment_refunds', 'policy_decision_id')
    op.drop_column('payment_refunds', 'proposal_id')
    op.drop_column('payment_refunds', 'resolution_case_id')
    op.drop_table('authorization_voids')
    op.drop_index(op.f('ix_resolution_approvals_proposal_id'), table_name='resolution_approvals')
    op.drop_table('resolution_approvals')
    op.drop_index(op.f('ix_financial_policy_decisions_inputs_sha256'), table_name='financial_policy_decisions')
    op.drop_table('financial_policy_decisions')
    op.drop_index(op.f('ix_resolution_proposals_case_id'), table_name='resolution_proposals')
    op.drop_table('resolution_proposals')
    op.drop_index(op.f('ix_resolution_events_case_id'), table_name='resolution_events')
    op.drop_table('resolution_events')
    op.drop_index('uq_resolution_cases_live_capture', table_name='resolution_cases', postgresql_where=sa.text("payment_capture_id IS NOT NULL AND status NOT IN ('resolved','closed_no_action','rejected','cancelled')"))
    op.drop_index(op.f('ix_resolution_cases_agreement_id'), table_name='resolution_cases')
    op.drop_table('resolution_cases')
    # ### end Alembic commands ###
