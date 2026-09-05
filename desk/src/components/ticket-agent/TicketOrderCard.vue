<template>
  <!-- S/6: the order basis ("Beställningsunderlag") sits under the
       classification row. It shows what the AI read out of the ticket, what
       is still missing, and one button that hands the row to the ERP wave.
       The button never creates an order; approve_extraction only marks the
       extraction ready and signs it with the agent's name. -->
  <div v-if="extraction" class="border-b px-5 py-3 text-sm">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-base-medium text-ink-gray-8">
        {{ __("Beställningsunderlag") }}
      </span>
      <Badge theme="blue" :label="__('AI-förslag')" />
      <Badge
        v-if="isComplete"
        theme="green"
        :label="__('KOMPLETT')"
      />
      <Badge v-else theme="orange" :label="__('OFULLSTÄNDIGT')" />

      <div class="ml-auto flex items-center gap-2">
        <Badge
          v-if="extraction.approved_by"
          theme="green"
          :label="__('Godkänd av {0}', [extraction.approved_by])"
        />
        <Tooltip v-else :text="approveHint">
          <div>
            <Button
              size="sm"
              variant="solid"
              :label="__('Använd i order')"
              :disabled="!isComplete"
              :loading="approve.loading"
              @click="approve.submit()"
            />
          </div>
        </Tooltip>
        <Button
          size="sm"
          variant="ghost"
          :label="showMore ? __('Visa mindre') : __('Visa mer')"
          @click="showMore = !showMore"
        />
      </div>
    </div>

    <dl class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3 lg:grid-cols-6">
      <div v-for="cell in mainCells" :key="cell.label">
        <dt class="text-xs text-ink-gray-5">{{ cell.label }}</dt>
        <dd class="truncate text-ink-gray-8" :title="cell.value">
          {{ cell.value || "–" }}
        </dd>
      </div>
    </dl>

    <p v-if="missing.length" class="mt-2 text-xs text-ink-amber-4">
      {{ __("Saknas") }}: {{ missingText }}
    </p>

    <dl
      v-if="showMore"
      class="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 border-t pt-2 sm:grid-cols-3"
    >
      <div v-for="cell in moreCells" :key="cell.label">
        <dt class="text-xs text-ink-gray-5">{{ cell.label }}</dt>
        <dd class="text-ink-gray-8">{{ cell.value || "–" }}</dd>
      </div>
    </dl>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { TicketSymbol } from "@/types";
import { Badge, Button, Tooltip, createResource, dayjs, toast } from "frappe-ui";
import { computed, inject, ref, watch } from "vue";

const ticket = inject(TicketSymbol)!;

// Same no-auto + watch pattern as TicketClassificationRow: the ticket may
// not be loaded at mount, and an empty request is never retried.
const current = createResource({
  url: "helpdesk.api.order_extraction.ticket_extraction",
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) current.fetch();
  },
  { immediate: true }
);

// The endpoint answers null for a ticket without an extraction; the card then
// takes no room at all.
const extraction = computed(() => (current.data?.name ? current.data : null));

// Field names are what the AI stores; the agent reads Swedish labels.
const FIELD_LABELS: Record<string, string> = {
  customer: __("Kund"),
  product: __("Produkt"),
  quantity: __("Antal"),
  size: __("Storlek"),
  colors: __("Färger"),
  production_option: __("Produktionsalternativ"),
  delivery_information: __("Leverans"),
  original_files: __("Originalfiler"),
};

function labelOf(field: string): string {
  return FIELD_LABELS[field] || field;
}

const missing = computed<string[]>(() => {
  let raw = extraction.value?.missing_fields;
  if (!raw) return [];
  if (typeof raw === "string") {
    try {
      raw = JSON.parse(raw);
    } catch {
      return [raw];
    }
  }
  return Array.isArray(raw) ? raw.map(String) : [];
});

const missingText = computed(() => missing.value.map(labelOf).join(", "));

// Completeness is the server's verdict (record_extraction/correct_extraction
// derive it); the card only reads it.
const isComplete = computed(
  () => !!extraction.value?.complete && missing.value.length === 0
);

const approveHint = computed(() =>
  isComplete.value
    ? __("Markera underlaget som klart för order")
    : __("Kan inte användas: {0} saknas", [missingText.value])
);

const showMore = ref(false);

const mainCells = computed(() => {
  const e = extraction.value || {};
  const quantity =
    e.quantity === null || e.quantity === undefined || e.quantity === 0
      ? ""
      : String(Number(e.quantity));
  return [
    { label: __("Beställningstyp"), value: ticket.value?.doc?.ticket_type || "" },
    { label: __("Produkt"), value: e.product || "" },
    { label: __("Storlek"), value: e.size || "" },
    { label: __("Färger"), value: e.colors || "" },
    { label: __("Antal"), value: quantity },
    { label: __("Leverans"), value: e.delivery_information || "" },
  ];
});

const moreCells = computed(() => {
  const e = extraction.value || {};
  const when = (v: unknown) => (v ? dayjs(String(v)).format("YYYY-MM-DD HH:mm") : "");
  return [
    { label: __("Kund"), value: e.customer || e.unresolved_customer || "" },
    { label: __("Produktionsalternativ"), value: e.production_option || "" },
    { label: __("Originalfiler"), value: e.original_files || "" },
    { label: __("Bilagor"), value: e.attachment_assessment || "" },
    { label: __("Repeatorder"), value: e.repeat_order ? __("Ja") : __("Nej") },
    { label: __("Status"), value: e.status || "" },
    { label: __("Rättad av"), value: e.corrected_by || "" },
    { label: __("Godkänd"), value: when(e.approved_on) },
    { label: __("Modell"), value: e.model_version || "" },
  ];
});

const approve = createResource({
  url: "helpdesk.api.order_extraction.approve_extraction",
  makeParams: () => ({ extraction_id: extraction.value?.name }),
  onSuccess() {
    current.fetch();
    toast.success(__("Underlaget är markerat som klart för order"));
  },
  onError(err: any) {
    toast.error(err?.messages?.[0] || __("Underlaget kunde inte godkännas"));
  },
});
</script>
