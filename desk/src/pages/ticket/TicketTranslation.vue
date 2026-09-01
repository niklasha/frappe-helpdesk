<template>
  <div
    v-if="inbound"
    class="mx-5 md:mx-10 mt-4 rounded border border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
  >
    <div class="flex items-center gap-2">
      <LanguagesIcon class="h-4 w-4 text-ink-gray-5" />
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
        class="ms-auto"
        variant="ghost"
        size="sm"
        :label="showOriginal ? __('Visa översättning') : __('Visa original')"
        @click="showOriginal = !showOriginal"
      />
    </div>

    <p class="mt-2 whitespace-pre-line text-base text-ink-gray-8">
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
  makeParams: () => ({ ticket_id: ticket.doc?.name }),
});

const languages = createResource({
  url: "helpdesk.api.translation.list_supported_languages",
  auto: true,
});

watch(
  () => ticket.doc?.name,
  (name) => {
    if (name) translations.fetch();
  },
  { immediate: true }
);

// The inbound one: what the customer wrote and an agent has to read. The
// outbound direction is the agent's own reply and belongs beside the editor,
// not here.
const inbound = computed(() =>
  (translations.data || []).find((row) => row.direction === "Inbound")
);

function languageName(code: string): string {
  const found = (languages.data || []).find((row) => row.language_code === code);
  // The code is a poor label but an honest one; inventing a name for a language
  // the catalogue does not carry would be worse.
  return found?.language_name || code;
}
</script>
