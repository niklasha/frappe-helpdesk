import { __ } from "@/translation";
import LucideAlarmClock from "~icons/lucide/alarm-clock";
import LucideCircleCheckBig from "~icons/lucide/circle-check-big";
import LucideInbox from "~icons/lucide/inbox";
import LucideMessageSquareMore from "~icons/lucide/message-square-more";
import LucideMessageSquareReply from "~icons/lucide/message-square-reply";
import LucideUser from "~icons/lucide/user";
import LucideUserX from "~icons/lucide/user-x";

/**
 * The seven work queues, in sidebar order. `key` is what
 * `helpdesk.api.queues.tickets`/`counts` understand and what the Tickets page
 * reads from `?queue=`; the sidebar and the page share this list so a label
 * never drifts from its filter.
 */
export const queueOptions = [
  { key: "all", label: __("Alla"), icon: LucideInbox },
  { key: "mine", label: __("Mina"), icon: LucideUser },
  { key: "unassigned", label: __("Otilldelade"), icon: LucideUserX },
  { key: "waiting_on_us", label: __("Väntar på oss"), icon: LucideMessageSquareReply },
  { key: "waiting_on_customer", label: __("Väntar på kund"), icon: LucideMessageSquareMore },
  { key: "sla_risk", label: __("SLA-risk"), icon: LucideAlarmClock },
  { key: "closed", label: __("Avslutade"), icon: LucideCircleCheckBig },
] as const;

export type QueueKey = (typeof queueOptions)[number]["key"];

export function queueLabel(key: string): string | undefined {
  return queueOptions.find((queue) => queue.key === key)?.label;
}

/** The sidebar's active-item key for a queue, distinct from route and view names. */
export function queueItemKey(key: string): string {
  return `queue:${key}`;
}
