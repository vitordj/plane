/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it, vi } from "vitest";
import type { IUnitQueue } from "@plane/types";
import { OrganizationalUnitStore } from "./organizational-unit.store";

/**
 * The queue half of the store (item 2.3).
 *
 * What is worth testing here is not that the store calls the service — it is
 * the three decisions the store makes on its own: it remembers the
 * capabilities the server sent alongside the rows, it answers "what may this
 * person do" with nothing until the server has said, and an action refreshes
 * the queue of the area it belongs to without letting a failed refresh
 * undo an action the person already saw succeed.
 */

const UNIT = "unit-1";
const PROJECT = "project-1";
const ISSUE = "issue-1";

const queuePayload = (overrides: Partial<IUnitQueue> = {}): IUnitQueue => ({
  capabilities: { can_claim: true, can_assign: false, can_return: false },
  items: [
    {
      id: "link-1",
      issue_id: ISSUE,
      sequence_id: 12,
      name: "Onboard the new hire",
      project: PROJECT,
      project_identifier: "ONB",
      target_date: null,
      state_group: "unstarted",
      state_name: "Todo",
      routing_state: "queued",
      queue_reason: "awaiting_coordinator",
      queued_at: "2026-09-06T10:00:00Z",
      assignment_due_at: null,
      assignment_overdue: false,
      age_seconds: 3600,
      last_alerted_at: null,
      primary_executor: null,
      primary_executor_detail: null,
      current_assignment_decision: "decision-1",
      process: null,
      created_at: "2026-09-06T10:00:00Z",
      updated_at: "2026-09-06T10:00:00Z",
    },
  ],
  ...overrides,
});

const makeStore = (service: Partial<OrganizationalUnitStore["service"]>) => {
  // The root store is only reached by the parts of this store that talk to
  // other stores, and the queue half does not.
  const store = new OrganizationalUnitStore({} as never);
  store.service = { ...store.service, ...service } as OrganizationalUnitStore["service"];
  return store;
};

describe("the area's queue in the store", () => {
  it("keeps the rows and the capabilities the server sent together", async () => {
    const getQueue = vi.fn().mockResolvedValue(queuePayload());
    const store = makeStore({ getQueue });

    const items = await store.fetchQueue("acme", UNIT);

    expect(getQueue).toHaveBeenCalledWith("acme", UNIT, undefined);
    expect(items).toHaveLength(1);
    expect(store.getQueueByUnitId(UNIT)[0].name).toBe("Onboard the new hire");
    expect(store.getCapabilitiesByUnitId(UNIT)).toEqual({
      can_claim: true,
      can_assign: false,
      can_return: false,
    });
  });

  it("claims nothing before the server has answered", () => {
    const store = makeStore({});

    // A capability assumed before it is granted draws a button whose only
    // outcome is a 403.
    expect(store.getCapabilitiesByUnitId("never-read")).toEqual({
      can_claim: false,
      can_assign: false,
      can_return: false,
    });
    expect(store.getQueueByUnitId("never-read")).toEqual([]);
  });

  it("passes the filters through untouched", async () => {
    const getQueue = vi.fn().mockResolvedValue(queuePayload());
    const store = makeStore({ getQueue });

    await store.fetchQueue("acme", UNIT, { routingState: "all", overdue: true });

    expect(getQueue).toHaveBeenCalledWith("acme", UNIT, { routingState: "all", overdue: true });
  });

  it("clears the loader when the read fails", async () => {
    const store = makeStore({ getQueue: vi.fn().mockRejectedValue(new Error("nope")) });

    await expect(store.fetchQueue("acme", UNIT)).rejects.toThrow("nope");
    expect(store.queueLoader).toBe(false);
  });
});

describe("the queue's actions", () => {
  it("claiming refreshes the area it belongs to", async () => {
    const getQueue = vi.fn().mockResolvedValue(queuePayload({ items: [] }));
    const claimIssue = vi.fn().mockResolvedValue({ routing: { routing_state: "assigned" } });
    const store = makeStore({ getQueue, claimIssue });

    const routing = await store.claim("acme", PROJECT, ISSUE, UNIT);

    expect(claimIssue).toHaveBeenCalledWith("acme", PROJECT, ISSUE);
    expect(routing.routing_state).toBe("assigned");
    expect(getQueue).toHaveBeenCalledTimes(1);
  });

  it("does not refresh anything when the caller did not name an area", async () => {
    const getQueue = vi.fn();
    const claimIssue = vi.fn().mockResolvedValue({ routing: { routing_state: "assigned" } });
    const store = makeStore({ getQueue, claimIssue });

    await store.claim("acme", PROJECT, ISSUE);

    expect(getQueue).not.toHaveBeenCalled();
  });

  it("assigning sends the decision the row was showing", async () => {
    const reassignIssue = vi.fn().mockResolvedValue({ routing: { routing_state: "assigned" } });
    const store = makeStore({ reassignIssue, getQueue: vi.fn().mockResolvedValue(queuePayload()) });

    await store.assign("acme", PROJECT, ISSUE, "user-9", {
      unitId: UNIT,
      expectedDecisionId: "decision-1",
      reason: "balancing",
    });

    expect(reassignIssue).toHaveBeenCalledWith("acme", PROJECT, ISSUE, "user-9", {
      reason: "balancing",
      expectedDecisionId: "decision-1",
    });
  });

  it("a failed refresh does not fail the action the person already saw succeed", async () => {
    const returnIssueToQueue = vi.fn().mockResolvedValue({ routing: { routing_state: "queued" } });
    const store = makeStore({
      returnIssueToQueue,
      getQueue: vi.fn().mockRejectedValue(new Error("network")),
    });

    const routing = await store.returnToQueue("acme", PROJECT, ISSUE, { unitId: UNIT });

    expect(routing.routing_state).toBe("queued");
  });

  it("a refused action reaches the caller", async () => {
    const store = makeStore({
      suspendIssue: vi.fn().mockRejectedValue({ data: { error_code: 4921 } }),
      getQueue: vi.fn(),
    });

    await expect(store.suspendIssue("acme", PROJECT, ISSUE, { unitId: UNIT })).rejects.toMatchObject({
      data: { error_code: 4921 },
    });
  });
});

describe("the coordinator roster in the store", () => {
  it("refetches after an appointment rather than patching it in", async () => {
    const addCoordinator = vi.fn().mockResolvedValue({ id: "coord-1" });
    const getCoordinators = vi.fn().mockResolvedValue([{ id: "coord-1", display_name: "Ana" }]);
    const store = makeStore({ addCoordinator, getCoordinators });

    await store.addCoordinator("acme", UNIT, "user-3");

    // The server reconciles native project access as part of the same
    // request, so its answer is the truth about what the appointment left.
    expect(getCoordinators).toHaveBeenCalledWith("acme", UNIT);
    expect(store.getCoordinatorsByUnitId(UNIT)).toHaveLength(1);
  });

  it("refetches after a removal too", async () => {
    const removeCoordinator = vi.fn().mockResolvedValue(undefined);
    const getCoordinators = vi.fn().mockResolvedValue([]);
    const store = makeStore({ removeCoordinator, getCoordinators });

    await store.removeCoordinator("acme", UNIT, "coord-1");

    expect(removeCoordinator).toHaveBeenCalledWith("acme", UNIT, "coord-1");
    expect(store.getCoordinatorsByUnitId(UNIT)).toEqual([]);
  });
});
