
// The endpoint carries the message date (source_message_on) beside the
// message name, so no second call is needed to say when the reply came.
const assessedAfter = computed(() => {
  const t = triage.data;
  if (!t?.source_message) return "";
  return t.source_message_on
    ? dayjs(t.source_message_on).format("YYYY-MM-DD HH:mm")
    : t.source_message;
});
<template>
  <!-- ticket_triage now always answers with a dict (AIAN-16); only one that
       carries a record name is a proposal worth a panel. -->
  <div v-if="triage.data?.name" class="flex flex-col gap-2.5 border-b px-6 py-3 sm:px-0">
    <!-- Label plus two badges does not fit one line on a phone; wrapping keeps
         them all readable instead of pushing the last one off the edge. -->
    <div class="flex flex-wrap items-center gap-2">
      <SparklesIcon class="h-4 w-4 text-ink-gray-5" />
      <span class="text-base-medium text-ink-gray-7">{{ __("AI:s förslag") }}</span>
      <!-- A proposal presented as a decision is a different product from the one
           the Kravspec asks for. The badge says which this is, always. -->
      <Badge
        :theme="needsReview ? 'orange' : 'blue'"
        :label="needsReview ? __('Kräver granskning') : __('Förslag')"
      />
      <Badge v-if="corrected" theme="green" :label="__('Rättad')" />
    </div>

    <dl class="flex flex-col gap-2">
      <div v-if="triage.data.classification">
        <dt class="text-sm text-ink-gray-5">{{ __("Handlar om") }}</dt>
        <dd class="text-base text-ink-gray-8">
          <!-- The correction wins where there is one: it is what a person
               decided, and showing the model's guess above it would invite
               the same correction again. -->
          {{ triage.data.corrected_classification || triage.data.classification }}
        </dd>
      </div>

      <div v-if="triage.data.priority">
        <dt class="text-sm text-ink-gray-5">{{ __("Föreslagen prioritet") }}</dt>
        <dd class="text-base text-ink-gray-8">{{ triage.data.priority }}</dd>
      </div>

      <div>
        <dt class="text-sm text-ink-gray-5">{{ __("Säkerhet") }}</dt>
        <dd class="flex items-center gap-2">
          <div class="h-1.5 w-24 rounded-full bg-surface-gray-2">
            <div
              class="h-1.5 rounded-full"
              :class="needsReview ? 'bg-surface-amber-3' : 'bg-surface-gray-6'"
              :style="{ width: confidencePercent + '%' }"
            />
          </div>
          <span class="text-sm tabular-nums text-ink-gray-7">
            {{ confidencePercent }}%
          </span>
        </dd>
      </div>

      <!-- AIAN-13: the agent must be able to see *why*. The record has carried
           this since Wave 2 and nothing ever showed it. -->
      <div v-if="triage.data.rationale">
        <dt class="text-sm text-ink-gray-5">{{ __("Varför") }}</dt>
        <dd class="break-words text-sm text-ink-gray-7">{{ triage.data.rationale }}</dd>
      </div>

      <div v-if="triage.data.missing_information">
        <dt class="text-sm text-ink-gray-5">{{ __("Saknas i beställningen") }}</dt>
        <dd class="break-words text-sm text-ink-gray-7">{{ triage.data.missing_information }}</dd>
      </div>
    </dl>

    <!-- AIAN-17: a verdict made after a customer reply says so, so a second
         reading is not presented as if it were the first. -->
    <p v-if="assessedAfter" class="text-xs text-ink-gray-5">
      {{ __("Bedömd efter meddelande {0}", [assessedAfter]) }}
    </p>

    <!-- Provenance last and quiet: an auditor needs it, an agent working a
         ticket does not read it every time. -->
    <p v-if="triage.data.model_version" class="text-xs text-ink-gray-4">
      {{ triage.data.model_version }}
      <span v-if="triage.data.prompt_version">· {{ __("prompt") }} {{ triage.data.prompt_version }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { Badge, createResource, dayjs } from "frappe-ui";
import { TicketSymbol } from "@/types";
import { computed, inject, watch } from "vue";
import SparklesIcon from "~icons/lucide/sparkles";

// The ticket comes from the same injection every other section in this
// sidebar reads, so the panel cannot be mounted somewhere the ticket is not.
const ticket = inject(TicketSymbol)!;

const triage = createResource({
  url: "helpdesk.api.ai_triage.ticket_triage",
  // No auto: at mount the injected ticket may not exist yet, and the request
  // would go out with an empty body and never be retried.
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
});

watch(
  // The symbol carries a ComputedRef, so script scope has to unwrap it; reading
  // `.doc` off the ref itself is undefined forever and the fetch never fires.
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) triage.fetch();
  },
  { immediate: true }
);

// AIAN-12: below the threshold the proposal is not one to act on, and the panel
// has to say so rather than leave the number to be read carefully.
const needsReview = computed(() => Boolean(triage.data?.requires_human_review));

const corrected = computed(() => Boolean(triage.data?.corrected_classification));

const confidencePercent = computed(() =>
  Math.round((Number(triage.data?.confidence) || 0) * 100)
);
</script>
