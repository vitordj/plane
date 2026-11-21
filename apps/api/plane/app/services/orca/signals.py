# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Signal receivers for the Orca layer.

Registered from ``plane.app.apps.AppApiConfig.ready()``. Everything here is a
sidecar behavior attached to a core model from the outside, per FORK.md — no
core model gains a column and no upstream call site is patched.
"""

# Django imports
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

# Module imports
from plane.db.models import IssueAssignee, Profile, UserLanguagePreference
from plane.license.models import InstanceConfiguration

from .language import DEFAULT_LANGUAGE_KEY, follower_profiles, get_default_language, normalize_language

# Set on a Profile instance while this module is the one changing its language,
# so the receiver that records a personal choice can tell its own writes apart
# from somebody using the language picker.
_SEEDING = "_orca_seeding_language"
# Where pre_save stashes the language as it stands in the database, so post_save
# can see whether this save changed it.
_PREVIOUS = "_orca_previous_language"
# The same trick for the instance setting: post_save needs the default that is
# being replaced in order to tell who was following it.
_PREVIOUS_DEFAULT = "_orca_previous_default_language"
# Soft-delete of an IssueAssignee: pre_save remembers deleted_at so post_save
# can tell a newly-stamped row from one that was already gone.
_PREVIOUS_DELETED_AT = "_orca_previous_assignee_deleted_at"


@receiver(post_save, sender=Profile)
def seed_profile_language(sender, instance, created, **kwargs):
    """Give a brand-new profile the instance's default interface language.

    @description ``Profile.language`` is born as ``"en"`` for everybody, which
        on a single-organization instance means every person has to go and
        change it once. This applies the instance default instead, at the one
        moment there is no user preference to overwrite: profile creation.

        A receiver rather than an edit to the four places that create profiles
        (the OAuth adapter, the magic-link view, the redirection backfill and
        the god-mode first admin) — all four are upstream files, and patching
        them would be four merge conflicts on every upstream sync for one
        behavior.

        Only a profile still holding the field's own default is touched. Code
        that creates a profile with an explicit language keeps it. At creation
        nobody has expressed a preference yet, so treating the model default as
        "unset" costs nothing; from here on the person's own choice is theirs
        and this receiver never fires for it again.
    @param created: Django's flag; updates are ignored entirely.
    @returns: None. Saves at most one field, and only when it would change.
    """
    if not created:
        return

    field_default = Profile._meta.get_field("language").get_default()
    if instance.language != field_default:
        return

    default_language = get_default_language()
    if default_language == instance.language:
        return

    instance.language = default_language
    # The flag keeps record_explicit_language_choice from reading this write as
    # the person picking a language. update_fields keeps it to a single-column
    # UPDATE, and the guards above make the re-entrant post_save a no-op.
    setattr(instance, _SEEDING, True)
    try:
        instance.save(update_fields=["language"])
    finally:
        delattr(instance, _SEEDING)


@receiver(pre_save, sender=Profile)
def stash_previous_language(sender, instance, **kwargs):
    """Remember the stored language so post_save can see whether it changed.

    @description Django hands post_save the saved row, not the one it replaced,
        so the comparison has to be set up here. Reads one column, and only for
        a profile that already exists.
    """
    if instance._state.adding or instance.pk is None:
        setattr(instance, _PREVIOUS, None)
        return
    setattr(
        instance,
        _PREVIOUS,
        Profile.objects.filter(pk=instance.pk).values_list("language", flat=True).first(),
    )


@receiver(post_save, sender=Profile)
def record_explicit_language_choice(sender, instance, created, **kwargs):
    """Note that this person's language is theirs, not the organization's.

    @description ``Profile.language`` is one column holding a code, so it cannot
        distinguish somebody who chose English from somebody who never opened
        the setting. Both read ``en``, and that ambiguity is what stopped the
        instance default from ever reaching existing members safely.

        A change to the column that this module did not make is somebody using
        the language picker, so it writes the sidecar row that says so. From
        then on nothing moves their language but them: not the rollout command,
        not a later change to the organization's default.

        Deliberately watches the column rather than the profile endpoint. The
        endpoint is upstream, and the picker is not the only thing that can set
        a language — an IdP sync or a future onboarding step would be missed.
    @returns: None. Writes at most one small row, once per person.
    """
    if created or getattr(instance, _SEEDING, False):
        return

    previous = getattr(instance, _PREVIOUS, None)
    if previous is None or previous == instance.language:
        return

    UserLanguagePreference.objects.update_or_create(
        user_id=instance.user_id,
        defaults={"follows_organization_default": False},
    )


@receiver(pre_save, sender=InstanceConfiguration)
def stash_previous_default_language(sender, instance, **kwargs):
    """Remember the organization's language before this save replaces it.

    @description The people who need to move when the default changes are the
        ones sitting on the *old* default, and post_save only sees the new one.
        Reads a single column, and only for the one key this module cares
        about.
    """
    if instance.key != DEFAULT_LANGUAGE_KEY:
        return

    stored = None
    if not instance._state.adding and instance.pk is not None:
        stored = InstanceConfiguration.objects.filter(pk=instance.pk).values_list("value", flat=True).first()
    # No previous row means the organization never had a default, so the people
    # following it are the ones still on the language every profile is born in.
    setattr(
        instance,
        _PREVIOUS_DEFAULT,
        normalize_language(stored) if stored is not None else Profile._meta.get_field("language").get_default(),
    )


@receiver(post_save, sender=InstanceConfiguration)
def apply_default_language_to_followers(sender, instance, **kwargs):
    """Move everyone who has not chosen onto the organization's new language.

    @description Without this the default only reaches profiles created after
        it was set, which makes changing it in god-mode look broken: the
        sign-in screen switches and everybody's workspace stays as it was.

        Only people who follow the organization move — see
        ``follower_profiles`` for what that means and why a language somebody
        set for themselves is never touched, sidecar row or not.

        One UPDATE, and ``queryset.update`` skips signals, so it cannot be
        mistaken for the choices it is careful not to touch.
    @returns: None.
    """
    if instance.key != DEFAULT_LANGUAGE_KEY:
        return

    language = normalize_language(instance.value)
    previous = getattr(instance, _PREVIOUS_DEFAULT, None)
    if previous is None or previous == language:
        return

    follower_profiles(previous).exclude(language=language).update(language=language, updated_at=timezone.now())


def _current_user():
    """
    @description The authenticated user behind the HTTP request being served, or ``None``.

    The native assignee edit goes through upstream serializers that do not hand
    their request to the sidecar seam, so the actor is read the same way
    ``BaseModel`` reads it for ``created_by``: from ``crum``. Outside a request
    (Celery, shell) there is none.
    """
    try:
        from crum import get_current_user

        user = get_current_user()
        if user is not None and getattr(user, "is_authenticated", False) and getattr(user, "pk", None):
            return user
    except Exception:  # noqa: BLE001 - the actor is a nicety, never a requirement
        pass
    return None


def _eligible_native_assignees(link) -> list:
    """
    @description The native assignees of the item who may execute it for this link's area on its project.

    Uses ``_assert_eligible`` — the check ``reassign`` makes — so adoption never
    accepts somebody a coordinator could not have picked.
    @returns User ids, in assignment order.
    """
    from plane.db.models import IssueAssignee as Assignee

    from .assignment_service import _assert_eligible
    from .errors import ExecutorNotEligible

    eligible = []
    for user_id in (
        Assignee.objects.filter(issue_id=link.issue_id).order_by("created_at").values_list("assignee_id", flat=True)
    ):
        try:
            _assert_eligible(link.organizational_unit, link.project_id, user_id)
        except ExecutorNotEligible:
            continue
        eligible.append(user_id)
    return eligible


def _adopt_native_assignee(link, *, actor=None) -> bool:
    """
    Make the one eligible native assignee the area's executor (R2.05).

    @description Native Plane and the area must not disagree about who is
    executing an item. When the native assignee edit leaves exactly one person
    who could execute it for this area (``_assert_eligible``), that person
    becomes ``primary_executor`` through ``reassign`` — so an ``AssignmentDecision``
    and the link update are written like any other reassignment, with ``actor``
    as ``decided_by``. ``reassign`` finds the ``IssueAssignee`` row already there
    and so writes no second native activity or notification.

    Adoption is skipped (``False``) when the person count is not exactly one, or
    when the governing policy's ``allowed_modes`` forbids the way the person got
    there: the native edit is a *manual* assignment, or a *self-claim* when the
    assignee is the person who made the edit. A mode the policy forbids is
    refused, never quietly degraded (I7).
    @param link: The ``IssueOrganizationalUnit`` (``assigned`` to someone no
        longer on the item, or waiting with nobody).
    @param actor: Who made the native edit, when known.
    @returns ``True`` when the item needs no further action (adopted, or it
        moved concurrently and is somebody else's now); ``False`` when the
        caller should fall back (requeue / leave waiting).
    """
    from plane.db.models import AssignmentMode

    from .assignment_service import reassign, resolve_policy
    from .errors import DecisionStale, ExecutorNotEligible

    candidates = _eligible_native_assignees(link)
    if len(candidates) != 1:
        return False
    candidate_id = candidates[0]

    allowed = set(resolve_policy(link.organizational_unit, link.project_id).allowed_modes)
    actor_id = getattr(actor, "id", None)
    is_self_claim = actor_id is not None and str(actor_id) == str(candidate_id)
    if AssignmentMode.MANUAL not in allowed and not (is_self_claim and AssignmentMode.SELF_CLAIM in allowed):
        return False

    try:
        reassign(
            link.issue,
            candidate_id,
            actor=actor,
            reason="adopted from a native assignee edit",
            expected_decision_id=link.current_assignment_decision_id,
        )
    except DecisionStale:
        # Somebody decided in between; their decision stands.
        return True
    except ExecutorNotEligible:
        return False
    return True


def _return_if_executor(issue_id, assignee_id, actor=None) -> None:
    """
    Reconcile the area's link when its executor was taken off the work item.

    @description The one case worth acting on: somebody clears the assignee in
    the app, and the area's link keeps saying "assigned" to a person who is no
    longer on the work item. Nobody sees it in a queue, nobody sees it in their
    own list, and it stops moving.

    If exactly one of the remaining native assignees can execute it for the area
    (``_adopt_native_assignee``) that person becomes the executor; otherwise the
    item goes back to the queue. Orca's own writes cannot loop back here:
    ``reassign`` keeps the native row, and ``return_to_queue`` removes the
    executor's row only after the link has left ``assigned``.

    Respects the master kill switch ``ORCA_ORG_UNITS_ENABLED`` (R2.20) and
    ignores closed/archived/deleted/draft items (R2.06). Anything that goes
    wrong here is swallowed so a sidecar failure never rolls back an ordinary
    assignee edit.
    @param issue_id: The work item.
    @param assignee_id: Who was removed.
    @param actor: Who made the edit; defaults to the user of the current request.
    @returns: None.
    """
    from plane.db.models import IssueAssignee as Assignee, IssueOrganizationalUnit
    from plane.db.models.organizational_unit import QueueReason, RoutingState

    from .feature_flags import organizational_units_enabled
    from .queue import open_work_links

    if not organizational_units_enabled():
        return

    try:
        link = (
            open_work_links(
                IssueOrganizationalUnit.objects.filter(
                    issue_id=issue_id,
                    primary_executor_id=assignee_id,
                    routing_state=RoutingState.ASSIGNED,
                )
            )
            .select_related("issue", "organizational_unit")
            .first()
        )
        if link is None:
            return
        # Re-added in the same transaction, or another row still covers them:
        # nothing has actually been taken away.
        if Assignee.objects.filter(issue_id=issue_id, assignee_id=assignee_id).exists():
            return

        actor = actor or _current_user()
        if _adopt_native_assignee(link, actor=actor):
            return

        from .assignment_service import return_to_queue

        return_to_queue(
            link.issue,
            actor=None,
            reason="the executor was removed from the work item",
            queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
            trigger="availability",
        )
    except Exception:  # noqa: BLE001 - never break an ordinary assignee edit
        from plane.utils.exception_logger import log_exception

        log_exception(Exception(f"orca: could not requeue issue {issue_id} after assignee removal"))


def reconcile_issue_assignees(issue_id, actor=None) -> None:
    """
    Sync area routing after a bulk/queryset native assignee replacement (R2.05).

    @description ``IssueCreateSerializer.update`` (app and public API) and
    ``BulkOperationsEndpoint`` replace ``IssueAssignee`` rows via
    ``QuerySet.delete()`` + ``bulk_create()``, which bypass Django signals.
    Calling this sidecar helper immediately after keeps
    ``IssueOrganizationalUnit`` in step in both directions, without ever
    failing the native edit:

    * the link is ``assigned`` and its executor is no longer on the item: the one
      eligible remaining assignee is adopted as executor, else the item goes back
      to the queue (``_return_if_executor``);
    * the link is waiting (``queued`` / ``allocation_failed``) with nobody and a
      single eligible person was just put on the item natively: adopt them (what
      somebody does when they ignore the inbox and assign from the item).
    @param issue_id: The work item whose assignees were just replaced.
    @param actor: Who made the edit; defaults to the user of the current request.
    @returns: None.
    """
    from plane.db.models import IssueAssignee as Assignee, IssueOrganizationalUnit
    from plane.db.models.organizational_unit import RoutingState

    from .feature_flags import organizational_units_enabled
    from .queue import open_work_links

    if not organizational_units_enabled():
        return

    try:
        link = (
            open_work_links(IssueOrganizationalUnit.objects.filter(issue_id=issue_id))
            .select_related("issue", "organizational_unit")
            .first()
        )
        if link is None:
            return

        if link.routing_state == RoutingState.ASSIGNED and link.primary_executor_id is not None:
            _return_if_executor(issue_id, link.primary_executor_id, actor=actor)
        elif link.routing_state in (RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED):
            if Assignee.objects.filter(issue_id=issue_id).exists():
                _adopt_native_assignee(link, actor=actor or _current_user())
    except Exception:  # noqa: BLE001 - never break an ordinary assignee edit
        from plane.utils.exception_logger import log_exception

        log_exception(Exception(f"orca: could not reconcile routing for issue {issue_id}"))


@receiver(pre_save, sender=IssueAssignee)
def stash_previous_deleted_at(sender, instance, **kwargs):
    """@description Remember whether the row was live, so post_save can see a soft delete."""
    if instance._state.adding or instance.pk is None:
        setattr(instance, _PREVIOUS_DELETED_AT, None)
        return
    setattr(
        instance,
        _PREVIOUS_DELETED_AT,
        IssueAssignee.all_objects.filter(pk=instance.pk).values_list("deleted_at", flat=True).first(),
    )


@receiver(post_save, sender=IssueAssignee)
def requeue_on_soft_deleted_assignee(sender, instance, created, **kwargs):
    """
    Return work to the queue when its executor is taken off it.

    @description Plane soft-deletes an assignee by stamping ``deleted_at``, so
    the removal arrives here as a save rather than a delete.

    **This does not see every removal.** ``QuerySet.delete()`` is a queryset
    ``UPDATE`` and fires no signal at all. Updating a work item's assignees in
    the app or through the public API, and the bulk-operations endpoint, use
    exactly that, so each of the three calls ``reconcile_issue_assignees``
    right after the edit (a minimal seam in the upstream-owned serializers and
    view). What this receiver buys is the single-row paths (``IssueAssignee``
    ``.delete()`` on an instance, cascades). Anything still missed is caught by
    the hourly ``sweep_routing_drift`` (which also alerts the coordinators) and
    by ``sweep_unavailable_executors``.
    @returns: None.
    """
    if created or instance.deleted_at is None:
        return
    if getattr(instance, _PREVIOUS_DELETED_AT, None) is not None:
        return

    _return_if_executor(instance.issue_id, instance.assignee_id)


@receiver(post_delete, sender=IssueAssignee)
def requeue_on_hard_deleted_assignee(sender, instance, **kwargs):
    """@description The same, for a hard delete — cascades and ``delete(soft=False)``."""
    _return_if_executor(instance.issue_id, instance.assignee_id)


# Where pre_save stashes the state an Issue is leaving, so post_save can tell a reopen.
_PREVIOUS_STATE_ID = "_orca_previous_state_id"


@receiver(pre_save, sender="db.Issue")
def stash_previous_issue_state(sender, instance, **kwargs):
    """
    @description Remember the state a work item is leaving (R2.06 reopen clock).

    ``Issue`` tracks ``state_id`` in memory (``ChangeTrackerMixin``), so this
    costs no query; it only records something when the state actually changed.
    """
    if instance._state.adding or not instance.has_changed("state_id"):
        setattr(instance, _PREVIOUS_STATE_ID, None)
        return
    setattr(instance, _PREVIOUS_STATE_ID, instance.old_values.get("state_id"))


def restart_waiting_clock_if_reopened(issue_id, previous_state_id, new_state_id) -> bool:
    """
    Give a reopened item a fresh waiting clock in its area's queue (R2.06).

    @description Shared by ``restart_waiting_clock_on_reopen`` (single-row
    ``Issue.save()``) and ``BulkOperationsEndpoint`` (``bulk_update`` seam).
    When a transition moves a work item out of a completed/cancelled state
    while its link is still waiting, resets ``queued_at``, recomputes
    ``assignment_due_at`` from the governing policy's SLA, and clears
    ``last_alerted_at``.
    @param issue_id: Primary key of the work item.
    @param previous_state_id: State pk before the update.
    @param new_state_id: State pk after the update.
    @returns True when the clock was restarted, False otherwise. Never raises.
    """
    if previous_state_id is None or previous_state_id == new_state_id:
        return False

    from datetime import timedelta

    from plane.db.models import IssueOrganizationalUnit, State
    from plane.db.models.organizational_unit import RoutingState

    from .assignment_service import CLOSED_STATE_GROUPS, resolve_policy
    from .feature_flags import organizational_units_enabled

    if not organizational_units_enabled():
        return False

    try:
        link = (
            IssueOrganizationalUnit.objects.filter(
                issue_id=issue_id,
                routing_state__in=(RoutingState.QUEUED, RoutingState.ALLOCATION_FAILED),
            )
            .select_related("organizational_unit")
            .first()
        )
        if link is None:
            return False
        groups = dict(State.objects.filter(pk__in=[previous_state_id, new_state_id]).values_list("id", "group"))
        was_closed = groups.get(previous_state_id) in CLOSED_STATE_GROUPS
        is_open = groups.get(new_state_id) not in CLOSED_STATE_GROUPS
        if not (was_closed and is_open):
            return False

        now = timezone.now()
        sla_seconds = resolve_policy(link.organizational_unit, link.project_id).sla_seconds
        link.queued_at = now
        link.assignment_due_at = now + timedelta(seconds=sla_seconds) if sla_seconds else None
        link.last_alerted_at = None
        link.save(update_fields=["queued_at", "assignment_due_at", "last_alerted_at", "updated_at"])
        return True
    except Exception:  # noqa: BLE001 - never break an ordinary state edit
        from plane.utils.exception_logger import log_exception

        log_exception(Exception(f"orca: could not restart the waiting clock for reopened issue {issue_id}"))
        return False


@receiver(post_save, sender="db.Issue")
def restart_waiting_clock_on_reopen(sender, instance, created, **kwargs):
    """
    Give a reopened item a fresh waiting clock in its area's queue (R2.06).

    @description Delegates to ``restart_waiting_clock_if_reopened`` for
    single-row ``Issue.save()`` calls; bulk state updates call the same helper
    from the Orca seam in ``BulkOperationsEndpoint``.
    @returns: None. Never raises into the state edit.
    """
    previous_state_id = getattr(instance, _PREVIOUS_STATE_ID, None)
    if created or previous_state_id is None:
        return
    setattr(instance, _PREVIOUS_STATE_ID, None)
    restart_waiting_clock_if_reopened(instance.pk, previous_state_id, instance.state_id)


# Track prior active/deleted state on WorkspaceMember so post_save knows when a
# member has just been deactivated or soft-deleted (R2.09).
_PREVIOUS_WM_STATE = "_orca_previous_wm_state"


def _return_departed_executor_items(workspace_id, user_id) -> None:
    """
    Put every open item a departed member was executing back in its area's queue.

    @description Deactivating the area membership is not enough: the link would
    stay ``assigned`` to somebody who can no longer work. Each open ``assigned``
    link in the workspace naming ``user_id`` as executor goes back through
    ``return_to_queue`` (reason ``executor_unavailable``, trigger ``availability``),
    which also takes them off the native assignees with history, and the area's
    coordinators get the same ``executor_unavailable`` alert the availability
    sweep raises (on commit, once per item). One item failing does not stop the rest.
    @param workspace_id: The workspace the member left.
    @param user_id: The departed user.
    """
    from django.db import transaction

    from plane.db.models import IssueOrganizationalUnit
    from plane.db.models.organizational_assignment import DecisionTrigger
    from plane.db.models.organizational_unit import QueueReason, RoutingState
    from plane.utils.exception_logger import log_exception

    from .alerts import notify_executor_unavailable_safely
    from .assignment_service import return_to_queue
    from .queue import open_work_links

    links = list(
        open_work_links(
            IssueOrganizationalUnit.objects.filter(
                workspace_id=workspace_id,
                primary_executor_id=user_id,
                routing_state=RoutingState.ASSIGNED,
            )
        ).select_related("issue")
    )
    for link in links:
        try:
            result = return_to_queue(
                link.issue,
                actor=None,
                reason="the executor left the workspace",
                queue_reason=QueueReason.EXECUTOR_UNAVAILABLE,
                trigger=DecisionTrigger.AVAILABILITY,
            )
            link_id = result.link.pk
            transaction.on_commit(lambda link_id=link_id: notify_executor_unavailable_safely(link_id))
        except Exception as error:  # noqa: BLE001 - one item must not stop the rest
            log_exception(error)


def _deactivate_member_org_ties(workspace_member_id, workspace_id, user_id=None) -> None:
    """
    Withdraw area memberships and coordinator roles when someone leaves a workspace (R2.09).

    @description Keeps unit rosters, coordinator lists, and the single-active-lead
    constraint accurate when a member leaves or is removed via upstream's
    workspace-member routes without patching upstream views (FORK.md). Also
    schedules access reconciliation so the grant ledger is cleaned up, and
    returns the departed member's open assigned items to their areas' queues
    (``_return_departed_executor_items``).
    @param user_id: The departed user, when the caller has it (the ``WorkspaceMember``
        instance does); looked up from the row otherwise.
    """
    from django.db import transaction

    from plane.db.models import OrganizationalUnitCoordinator, OrganizationalUnitMembership

    from .feature_flags import organizational_units_enabled

    if not organizational_units_enabled():
        return

    try:
        now = timezone.now()
        deactivated_memberships = OrganizationalUnitMembership.objects.filter(
            workspace_member_id=workspace_member_id,
            is_active=True,
        ).update(is_active=False, updated_at=now)
        deactivated_coordinators = OrganizationalUnitCoordinator.objects.filter(
            workspace_member_id=workspace_member_id,
            is_active=True,
        ).update(is_active=False, updated_at=now)

        if user_id is not None:
            _return_departed_executor_items(workspace_id, user_id)

        if deactivated_memberships or deactivated_coordinators:

            def _reconcile():
                try:
                    from .org_unit_reconciler import dispatch_reconciliation

                    dispatch_reconciliation(workspace_id, member_ids=[workspace_member_id])
                except Exception:  # noqa: BLE001 - never fail member removal on async reconcile
                    from plane.utils.exception_logger import log_exception

                    log_exception(
                        Exception(f"orca: could not reconcile grants after workspace member {workspace_member_id} left")
                    )

            transaction.on_commit(_reconcile)
    except Exception:  # noqa: BLE001 - never break workspace member removal
        from plane.utils.exception_logger import log_exception

        log_exception(Exception(f"orca: could not deactivate unit ties for workspace member {workspace_member_id}"))


@receiver(pre_save, sender="db.WorkspaceMember")
def stash_previous_workspace_member_state(sender, instance, **kwargs):
    """
    @description Remember whether this WorkspaceMember was active before the save (R2.09).

    Runs on every ``WorkspaceMember.save()``, so it returns before touching the
    database when the layer is off, or when ``update_fields`` shows the save
    cannot change ``is_active`` or ``deleted_at`` (a role edit, say).
    """
    from .feature_flags import organizational_units_enabled

    if not organizational_units_enabled():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not {"is_active", "deleted_at"} & set(update_fields):
        return
    if instance._state.adding or instance.pk is None:
        setattr(instance, _PREVIOUS_WM_STATE, None)
        return
    row = sender.all_objects.filter(pk=instance.pk).values_list("is_active", "deleted_at").first()
    setattr(instance, _PREVIOUS_WM_STATE, row)


@receiver(post_save, sender="db.WorkspaceMember")
def deactivate_org_ties_on_workspace_member_removal(sender, instance, created, **kwargs):
    """@description Deactivate area memberships and coordinators when a WorkspaceMember becomes inactive (R2.09)."""
    if created:
        return
    previous = getattr(instance, _PREVIOUS_WM_STATE, None)
    was_active = previous is not None and previous[0] is True and previous[1] is None
    now_inactive = not instance.is_active or instance.deleted_at is not None
    if was_active and now_inactive:
        _deactivate_member_org_ties(instance.pk, instance.workspace_id, instance.member_id)


@receiver(post_delete, sender="db.WorkspaceMember")
def deactivate_org_ties_on_workspace_member_hard_delete(sender, instance, **kwargs):
    """@description Same cleanup on hard delete of a WorkspaceMember (R2.09)."""
    _deactivate_member_org_ties(instance.pk, instance.workspace_id, instance.member_id)
