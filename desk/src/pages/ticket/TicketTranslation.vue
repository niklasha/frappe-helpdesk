<template>
  <div
    v-if="inbound"
    class="mx-5 md:mx-10 mt-4 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
  >
    <!-- On a phone the sentence and the button do not share a line; wrapping is
         what keeps the toggle reachable instead of squeezed to a stub. -->
    <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
      <LanguagesIcon class="h-4 w-4 shrink-0 text-ink-gray-5" />
      <span class="text-sm text-ink-gray-6">
        {{
          __("Ärendet kom på {0} och har översatts.").replace(
            "{0}",
            languageName(inbound.source_language)
          )
        }}
      </span>
      <!-- LANG-03: the original must always be available, which means one click
           away and labelled — not merely present in a record somewhere. -->
      <Button
        class="ms-auto shrink-0"
        variant="ghost"
        size="sm"
        :label="showOriginal ? __('Visa översättning') : __('Visa original')"
        @click="showOriginal = !showOriginal"
      />
    </div>

    <p class="mt-2 whitespace-pre-line break-words text-base text-ink-gray-8">
      {{ showOriginal ? inbound.original_text : inbound.translated_text }}
    </p>

    <p class="mt-2 text-xs text-ink-gray-4">
      {{ showOriginal ? __("Kundens egna ord") : __("Maskinöversatt") }}
      <span v-if="!showOriginal && inbound.model_version">· {{ inbound.model_version }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { Button, createResource } from "frappe-ui";
import { computed, inject, ref, watch } from "vue";
import LanguagesIcon from "~icons/lucide/languages";
import { TicketSymbol } from "@/types";

const ticket = inject(TicketSymbol)!;
const showOriginal = ref(false);

const translations = createResource({
  url: "helpdesk.api.translation.ticket_translations",
  // No auto, for the same reason as the triage panel: the ticket is injected
  // and may arrive after this component does.
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
});

const languages = createResource({
  url: "helpdesk.api.translation.list_supported_languages",
  auto: true,
});

watch(
  // The symbol carries a ComputedRef, so script scope has to unwrap it; reading
  // `.doc` off the ref itself is undefined forever and the fetch never fires.
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) translations.fetch();
  },
  { immediate: true }
);

// The inbound one that belongs to no message: the ticket's own description.
//
// Since Wave 13 a translation that names a message is rendered inside that
// message, so showing it here as well would put the same words on the screen
// twice. What is left for this band is the case the thread cannot cover — a
// ticket raised through the portal, whose description arrived as a field rather
// than as an email. When a ticket was opened by email the first message wears
// the description's translation through a row of its own that names it
// (`adopted_from`, Wave 17); the ticket's row stays the one that was paid for,
// so the band has to look past it, and correctly falls silent.
const inbound = computed(() => {
  const rows = translations.data || [];
  const worn = new Set(rows.map((row) => row.adopted_from).filter(Boolean));
  return rows.find(
    (row) => row.direction === "Inbound" && !row.message && !worn.has(row.name)
  );
});

function languageName(code: string): string {
  const found = (languages.data || []).find((row) => row.language_code === code);
  // The code is a poor label but an honest one; inventing a name for a language
  // the catalogue does not carry would be worse.
  return found?.language_name || code;
}
</script>
