/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { IQueueCapabilities, IQueueItem } from "@plane/types";

/**
 * `queue-list.tsx` is one decision — the list, or the empty state — plus the
 * count in the heading and the server's order left alone. That is what this
 * covers.
 *
 * The row is mocked deliberately. `queue-item-row.tsx` pulls in the store, the
 * router, the toast and five actions; rendering it here would make a test of
 * "does the section render its rows" fail for reasons that have nothing to do
 * with the section. What the section owes its child is the item and the
 * capabilities, so the mock records exactly that.
 */

const rendered: { item: IQueueItem; capabilities: IQueueCapabilities }[] = [];

vi.mock("./queue-item-row", () => ({
  QueueItemRow: (props: { item: IQueueItem; capabilities: IQueueCapabilities }) => {
    rendered.push({ item: props.item, capabilities: props.capabilities });
    return <div data-testid="queue-item-row">{props.item.name}</div>;
  },
}));

vi.mock("@plane/i18n", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const { QueueList } = await import("./queue-list");

const capabilities: IQueueCapabilities = { can_claim: true, can_assign: false, can_return: false };

const item = (overrides: Partial<IQueueItem> = {}): IQueueItem => ({
  id: "link-1",
  issue_id: "issue-1",
  sequence_id: 12,
  name: "Onboard the new hire",
  project: "project-1",
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
  process: null,
  current_assignment_decision: null,
  created_at: "2026-09-06T10:00:00Z",
  updated_at: "2026-09-06T10:00:00Z",
  ...overrides,
});

const renderList = (items: IQueueItem[]) => {
  rendered.length = 0;
  return render(
    <QueueList
      workspaceSlug="acme"
      unitId="unit-1"
      title="inbox.title"
      emptyLabel="inbox.empty"
      items={items}
      capabilities={capabilities}
      onAssign={vi.fn()}
      onTransfer={vi.fn()}
    />
  );
};

describe("QueueList", () => {
  it("renders one row per item", () => {
    renderList([item(), item({ id: "link-2", issue_id: "issue-2", name: "Collect the documents" })]);

    expect(screen.getAllByTestId("queue-item-row")).toHaveLength(2);
    expect(screen.getByText("Onboard the new hire")).toBeTruthy();
    expect(screen.getByText("Collect the documents")).toBeTruthy();
  });

  it("shows the empty label instead of an empty list", () => {
    renderList([]);

    expect(screen.queryByTestId("queue-item-row")).toBeNull();
    expect(screen.getByText("inbox.empty")).toBeTruthy();
  });

  it("puts the count next to the title, so a full inbox is visible without scrolling", () => {
    renderList([item(), item({ id: "link-2", issue_id: "issue-2" }), item({ id: "link-3", issue_id: "issue-3" })]);

    expect(screen.getByText("inbox.title")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });

  it("keeps the order it was given, because the server chose it", () => {
    // Overdue first, then longest waiting (see services/orca/queue.py). A
    // component that re-sorted would quietly overrule that.
    renderList([
      item({ id: "link-1", issue_id: "issue-1", name: "second oldest" }),
      item({ id: "link-2", issue_id: "issue-2", name: "oldest" }),
    ]);

    expect(rendered.map((call) => call.item.name)).toEqual(["second oldest", "oldest"]);
  });

  it("hands every row the capabilities the server sent", () => {
    // The rules live on the server; a row deriving its own would let somebody
    // who lost the coordinator role keep clicking buttons the API refuses.
    renderList([item(), item({ id: "link-2", issue_id: "issue-2" })]);

    expect(rendered.every((call) => call.capabilities === capabilities)).toBe(true);
  });
});
