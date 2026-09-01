import { createResource } from "frappe-ui";
import { computed, type ComputedRef } from "vue";

export interface MessageTranslation {
  name: string;
  message: string | null;
  direction: string;
  source_language: string;
  target_language: string;
  original_text: string;
  translated_text: string;
  model_version: string | null;
}

// One request per ticket, shared by every message in its thread. A resource per
// EmailArea would mean one call per message in the conversation, all asking the
// same question — and the thread is exactly where that multiplies.
const perTicket = new Map<string, ReturnType<typeof createResource>>();

function resourceFor(ticketId: string) {
  if (!perTicket.has(ticketId)) {
    perTicket.set(
      ticketId,
      createResource({
        url: "helpdesk.api.translation.ticket_translations",
        params: { ticket_id: ticketId },
        auto: true,
      })
    );
  }
  return perTicket.get(ticketId)!;
}

/**
 * The inbound translations recorded for one ticket, indexed by the message they
 * belong to.
 *
 * A translation with no message translates the ticket's own description, which
 * is what the ingress has always recorded; those are deliberately absent from
 * the index rather than folded into it, because attaching them to an arbitrary
 * message would claim the customer wrote those words there.
 */
export function useTicketTranslations(ticketId: ComputedRef<string | undefined>) {
  const resource = computed(() =>
    ticketId.value ? resourceFor(ticketId.value) : null
  );

  const byMessage = computed(() => {
    const rows: MessageTranslation[] = resource.value?.data ?? [];
    const index = new Map<string, MessageTranslation>();
    for (const row of rows) {
      if (row.direction === "Inbound" && row.message && row.translated_text) {
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
