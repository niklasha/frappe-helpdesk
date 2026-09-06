import { createResource } from "frappe-ui";
import { computed, type ComputedRef } from "vue";

export interface MessageDelivery {
  message: string;
  status: string | null;
  held: number;
}

// One request per ticket, shared by every message in its thread — the same
// reason useTicketTranslations does it: a resource per EmailArea would ask the
// same question once per message in the conversation.
const perTicket = new Map<string, ReturnType<typeof createResource>>();

function resourceFor(ticketId: string) {
  if (!perTicket.has(ticketId)) {
    perTicket.set(
      ticketId,
      createResource({
        url: "helpdesk.api.ticket.delivery_state",
        params: { ticket_id: ticketId },
        auto: true,
      })
    );
  }
  return perTicket.get(ticketId)!;
}

/**
 * What the outgoing queue says about each message of one ticket, indexed by the
 * message it belongs to.
 *
 * A message the queue never saw has no entry at all: the endpoint reports
 * absence as absence, and the thread must render such a message exactly as it
 * always did rather than claim it is waiting.
 */
export function useTicketDelivery(ticketId: ComputedRef<string | undefined>) {
  const resource = computed(() =>
    ticketId.value ? resourceFor(ticketId.value) : null
  );

  const byMessage = computed(() => {
    const rows: MessageDelivery[] = resource.value?.data ?? [];
    const index = new Map<string, MessageDelivery>();
    for (const row of rows) {
      if (row.message) {
        index.set(row.message, row);
      }
    }
    return index;
  });

  function forMessage(message: string | undefined) {
    return message ? byMessage.value.get(message) : undefined;
  }

  function reload() {
    resource.value?.reload();
  }

  return { byMessage, forMessage, reload };
}

/**
 * Refresh the queue's answer for one ticket, if the thread has asked for it.
 *
 * The cache above is filled once per ticket and never reloads by itself, so a
 * reply the agent has just sent would carry no badge — and a badge would stay
 * after someone hand-sent the mail — until a full page reload. `reloadTicket`
 * calls this wherever the thread itself refreshes.
 */
export function reloadTicketDelivery(ticketId: string) {
  perTicket.get(ticketId)?.reload();
}
