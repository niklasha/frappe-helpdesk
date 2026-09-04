<template>
  <!-- S-2026-09-04 area 3: the classification lives on the header line, not
       only in the sidebar. One row: what the ticket is, what the AI proposes,
       one button to apply it, one to edit by hand. -->
  <div
    class="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b px-5 py-2 text-sm"
  >
    <div class="flex items-center gap-1.5 text-ink-gray-8">
      <span class="text-ink-gray-5">{{ __("Ärendetyp") }}</span>
      <span class="text-base-medium">{{ ticket.doc?.ticket_type || __("Ej satt") }}</span>
      <template v-if="ticket.doc?.classification_model">
        <span class="text-ink-gray-4">›</span>
        <span>{{ ticket.doc.classification_model }}</span>
      </template>
    </div>

    <template v-if="proposal">
      <span class="hidden text-ink-gray-3 sm:inline">|</span>
      <div class="flex flex-wrap items-center gap-1.5 text-ink-gray-7">
        <SparklesIcon class="h-3.5 w-3.5 text-ink-gray-5" />
        <span class="text-ink-gray-5">{{ __("AI föreslår") }}</span>
        <span class="text-base-medium text-ink-gray-8">{{ proposedType }}</span>
        <span v-if="proposal.priority">· {{ proposal.priority }}</span>
        <span class="tabular-nums text-ink-gray-5">· {{ confidencePercent }} %</span>
        <Badge v-if="proposal.requires_human_review" theme="orange" :label="__('Kräver granskning')" />
      </div>

      <div class="flex items-center gap-1.5">
        <!-- Accepted: say who, and stop offering the button. Re-accepting an
             accepted proposal is a no-op the agent should not be tempted into. -->
        <Badge
          v-if="proposal.accepted_by"
          theme="green"
          :label="__('Använd av {0}', [proposal.accepted_by])"
        />
        <Button
          v-else
          size="sm"
          variant="solid"
          :label="__('Använd')"
          :loading="accept.loading"
          @click="accept.submit()"
        />
      </div>
    </template>

    <Button
      size="sm"
      variant="subtle"
      :label="__('Redigera klassificering')"
      @click="openEditor"
    />

    <!-- The endpoint refused part of the proposal in words; the agent has to
         read those words here, not discover them from a type that did not
         change. -->
    <p v-if="refusedText" class="basis-full text-xs text-ink-amber-4">
      {{ refusedText }}
    </p>

    <!-- S/4: "Behöver: …" — derived by ticket_triage, never stored. -->
    <p v-if="nextStep" class="basis-full text-xs text-ink-gray-6">
      {{ __("Behöver") }}: {{ nextStep }}
    </p>
  </div>

  <Dialog v-model:open="showEditor" :title="__('Redigera klassificering')">
    <template #default>
      <div class="flex flex-col gap-3">
        <FormControl
          type="select"
          :label="__('Ärendetyp')"
          v-model="editType"
          :options="typeOptions"
        />
        <FormControl
          type="select"
          :label="__('Prioritet')"
          v-model="editPriority"
          :options="priorityOptions"
        />
      </div>
    </template>
    <template #actions>
      <Button
        class="w-full"
        variant="solid"
        :label="__('Spara')"
        :loading="saving"
        @click="saveEdit"
      />
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { useTicketPriorityStore } from "@/stores/ticketPriority";
import { TicketSymbol } from "@/types";
import {
  Badge,
  Button,
  Dialog,
  FormControl,
  call,
  createListResource,
  createResource,
  toast,
} from "frappe-ui";
import { computed, inject, ref, watch } from "vue";
import SparklesIcon from "~icons/lucide/sparkles";

// Same injection every ticket-page section reads, so the row cannot be mounted
// where the ticket is not.
const ticket = inject(TicketSymbol)!;
const refreshTicket = inject<() => void>("refreshTicket", () => ticket.value?.reload?.());

const triage = createResource({
  url: "helpdesk.api.ai_triage.ticket_triage",
  // No auto: the injected ticket may not exist yet at mount, and an empty
  // request is never retried.
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) triage.fetch();
  },
  { immediate: true }
);

const proposal = computed(() => triage.data || null);

// next_step rides on the same response; null means the endpoint predates it.
const nextStep = computed(() => triage.data?.next_step || "");

const proposedType = computed(() => {
  const p = proposal.value;
  if (!p) return "";
  // The correction wins where there is one; else the catalogued type; the free
  // text only when the catalogue resolved nothing.
  return (
    p.corrected_ticket_type ||
    p.proposed_ticket_type ||
    p.corrected_classification ||
    p.classification ||
    __("Ingen typ i katalogen")
  );
});

const confidencePercent = computed(() =>
  Math.round((Number(proposal.value?.confidence) || 0) * 100)
);

// refused_fields is a JSON object {field: reason} written by accept_triage.
// It may also arrive as a string; both are read.
const refusedText = computed(() => {
  let refused = proposal.value?.refused_fields;
  if (!refused) return "";
  if (typeof refused === "string") {
    try {
      refused = JSON.parse(refused);
    } catch {
      return refused;
    }
  }
  const labels: Record<string, string> = {
    ticket_type: __("Ärendetyp"),
    priority: __("Prioritet"),
    agent_group: __("Team"),
  };
  return Object.entries(refused)
    .map(([field, reason]) => `${labels[field] || field}: ${reason}`)
    .join(" · ");
});

const accept = createResource({
  url: "helpdesk.api.ai_triage.accept_triage",
  makeParams: () => ({ triage_id: proposal.value?.name }),
  onSuccess(data: any) {
    // The endpoint returns the updated triage dict; showing it directly saves
    // a round trip, and the ticket reload brings the applied type/priority.
    if (data) triage.data = data;
    refreshTicket();
    toast.success(__("Förslaget är använt"));
  },
  onError(err: any) {
    toast.error(err?.messages?.[0] || __("Förslaget kunde inte användas"));
  },
});

// --- Manual editor -------------------------------------------------------

const showEditor = ref(false);
const editType = ref("");
const editPriority = ref("");
const saving = ref(false);

const ticketTypes = createListResource({
  doctype: "HD Ticket Type",
  cache: ["HD Ticket Type", "list", "classification-row"],
  fields: ["name", "classification_group"],
  filters: { disabled: 0 },
  pageLength: 1000,
});

const typeOptions = computed(() =>
  (ticketTypes.data || []).map((t: any) => ({
    label: t.classification_group ? `${t.name} › ${t.classification_group}` : t.name,
    value: t.name,
  }))
);

const priorityStore = useTicketPriorityStore();
const priorityOptions = computed(() =>
  (priorityStore.priorities.data || [])
    .filter((p: any) => !p.disabled)
    .map((p: any) => ({ label: p.name, value: p.name }))
);

function openEditor() {
  if (!ticketTypes.data) ticketTypes.fetch();
  // Start from the proposal when there is one and nothing was applied yet: the
  // dialog is where the agent changes the AI's guess, so the guess is the draft.
  const p = proposal.value;
  editType.value =
    (p && !p.accepted_by && (p.corrected_ticket_type || p.proposed_ticket_type)) ||
    ticket.value?.doc?.ticket_type ||
    "";
  editPriority.value =
    (p && !p.accepted_by && p.priority) || ticket.value?.doc?.priority || "";
  showEditor.value = true;
}

async function saveEdit() {
  const fieldname: Record<string, string> = {};
  if (editType.value && editType.value !== ticket.value?.doc?.ticket_type) {
    fieldname.ticket_type = editType.value;
  }
  if (editPriority.value && editPriority.value !== ticket.value?.doc?.priority) {
    fieldname.priority = editPriority.value;
  }
  if (!Object.keys(fieldname).length) {
    showEditor.value = false;
    return;
  }
  saving.value = true;
  try {
    await call("frappe.client.set_value", {
      doctype: "HD Ticket",
      name: ticket.value.doc.name,
      fieldname,
    });
    showEditor.value = false;
    refreshTicket();
    // A hand-set type may reopen an accepted proposal (AIAN-14); re-read it.
    triage.fetch();
    toast.success(__("Klassificeringen är sparad"));
  } catch (err: any) {
    toast.error(err?.messages?.[0] || __("Klassificeringen kunde inte sparas"));
  } finally {
    saving.value = false;
  }
}
</script>
