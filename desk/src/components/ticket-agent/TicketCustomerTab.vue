<template>
  <!-- S/7: the "Kund" tab. Everything the desk knows about the sender's
       company in one place: the profile the AI also reads (key account,
       default product, proof, delivery, notes), the customer's other open
       tickets, and the originals attached to its earlier tickets — so an
       agent never asks a key account for a product or a logo it already sent. -->
  <div class="flex flex-1 flex-col overflow-y-auto px-5 py-4 text-sm">
    <div v-if="profile.loading && !profile.data" class="text-ink-gray-5">
      {{ __("Hämtar kund…") }}
    </div>

    <div v-else-if="!customer" class="text-ink-gray-5">
      {{ __("Ingen kund kopplad — avsändardomänen matchar ingen kund") }}
    </div>

    <template v-else>
      <!-- Profile card -->
      <div class="rounded-lg border p-4">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-base-medium text-ink-gray-8">
            {{ customer.customer_name || customer.name }}
          </span>
          <Badge
            v-if="customer.key_account"
            theme="orange"
            :label="__('Nyckelkund')"
          />
          <Button
            class="ml-auto"
            size="sm"
            variant="subtle"
            :label="__('Redigera')"
            @click="openEdit"
          />
        </div>

        <dl class="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
          <div>
            <dt class="text-xs text-ink-gray-5">{{ __("Standardprodukt") }}</dt>
            <dd class="text-ink-gray-8">{{ customer.default_product || "–" }}</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-gray-5">{{ __("Korrektur krävs") }}</dt>
            <dd class="text-ink-gray-8">
              {{ customer.proof_required ? __("Ja") : __("Nej") }}
            </dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="text-xs text-ink-gray-5">{{ __("Standardleverans") }}</dt>
            <dd class="whitespace-pre-line text-ink-gray-8">
              {{ customer.delivery_default || "–" }}
            </dd>
          </div>
          <div class="sm:col-span-2">
            <dt class="text-xs text-ink-gray-5">{{ __("Anteckningar") }}</dt>
            <dd class="whitespace-pre-line text-ink-gray-8">
              {{ customer.desk_notes || "–" }}
            </dd>
          </div>
        </dl>
      </div>

      <!-- Open tickets -->
      <section class="mt-5">
        <h3 class="text-base-medium text-ink-gray-8">{{ __("Öppna ärenden") }}</h3>
        <p v-if="!openTickets.length" class="mt-1 text-ink-gray-5">
          {{ __("Inga andra öppna ärenden") }}
        </p>
        <ul v-else class="mt-1 space-y-1">
          <li v-for="t in openTickets" :key="t.name" class="flex gap-2">
            <RouterLink
              :to="{ name: 'TicketAgent', params: { ticketId: t.name } }"
              class="text-ink-blue-3 hover:underline"
            >
              #{{ t.name }}
            </RouterLink>
            <span class="truncate text-ink-gray-8" :title="t.subject">
              {{ t.subject }}
            </span>
            <Badge class="ml-auto" :label="t.status" />
          </li>
        </ul>
      </section>

      <!-- Recent tickets -->
      <section class="mt-5">
        <h3 class="text-base-medium text-ink-gray-8">{{ __("Senaste ärenden") }}</h3>
        <p v-if="!recentTickets.length" class="mt-1 text-ink-gray-5">
          {{ __("Inga tidigare ärenden") }}
        </p>
        <ul v-else class="mt-1 space-y-1">
          <li v-for="t in recentTickets" :key="t.name" class="flex gap-2">
            <RouterLink
              :to="{ name: 'TicketAgent', params: { ticketId: t.name } }"
              class="text-ink-blue-3 hover:underline"
            >
              #{{ t.name }}
            </RouterLink>
            <span class="truncate text-ink-gray-8" :title="t.subject">
              {{ t.subject }}
            </span>
            <span v-if="t.status" class="ml-auto text-xs text-ink-gray-5">
              {{ t.status }}
            </span>
          </li>
        </ul>
      </section>

      <!-- Earlier originals -->
      <section class="mt-5">
        <h3 class="text-base-medium text-ink-gray-8">{{ __("Tidigare original") }}</h3>
        <p v-if="!originals.length" class="mt-1 text-ink-gray-5">
          {{ __("Inga original på tidigare ärenden") }}
        </p>
        <ul v-else class="mt-1 space-y-1">
          <li
            v-for="f in originals"
            :key="f.file_url + f.ticket"
            class="flex gap-2"
          >
            <a
              :href="f.file_url"
              target="_blank"
              rel="noopener"
              class="truncate text-ink-blue-3 hover:underline"
              :title="f.file_name"
            >
              {{ f.file_name }}
            </a>
            <RouterLink
              :to="{ name: 'TicketAgent', params: { ticketId: f.ticket } }"
              class="ml-auto whitespace-nowrap text-xs text-ink-gray-5 hover:underline"
            >
              {{ __("från #{0}", [f.ticket]) }}
            </RouterLink>
          </li>
        </ul>
      </section>
    </template>

    <!-- Edit dialog: writes straight to HD Customer; the profile is data,
         not prompt text, so the AI sees the change on the next ticket. -->
    <Dialog v-model="showEdit" :options="{ title: __('Redigera kund') }">
      <template #body-content>
        <div class="flex flex-col gap-3">
          <FormControl
            type="checkbox"
            v-model="form.key_account"
            :label="__('Nyckelkund')"
          />
          <FormControl
            type="text"
            v-model="form.default_product"
            :label="__('Standardprodukt')"
          />
          <FormControl
            type="checkbox"
            v-model="form.proof_required"
            :label="__('Korrektur krävs')"
          />
          <FormControl
            type="textarea"
            v-model="form.delivery_default"
            :label="__('Standardleverans')"
          />
          <FormControl
            type="textarea"
            v-model="form.desk_notes"
            :label="__('Anteckningar')"
          />
        </div>
      </template>
      <template #actions>
        <Button
          class="w-full"
          variant="solid"
          :label="__('Spara')"
          :loading="save.loading"
          @click="save.submit()"
        />
      </template>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { TicketSymbol } from "@/types";
import {
  Badge,
  Button,
  Dialog,
  FormControl,
  createResource,
  toast,
} from "frappe-ui";
import { computed, inject, reactive, ref, watch } from "vue";

interface ProfileCustomer {
  name?: string;
  customer_name?: string;
  key_account?: number | boolean;
  default_product?: string;
  proof_required?: number | boolean;
  delivery_default?: string;
  desk_notes?: string;
  extra_domains?: string;
}

interface TicketRow {
  name: string;
  subject?: string;
  status?: string;
}

interface OriginalRow {
  file_name: string;
  file_url: string;
  ticket: string;
}

const ticket = inject(TicketSymbol)!;

// Same no-auto + watch pattern as TicketOrderCard: the ticket may not be
// loaded at mount, and an empty request is never retried.
const profile = createResource({
  url: "helpdesk.api.customer.customer_profile",
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) profile.fetch();
  },
  { immediate: true }
);

const customer = computed<ProfileCustomer | null>(() => {
  const c = profile.data?.customer;
  return c && (c.name || c.customer_name) ? c : null;
});
const openTickets = computed<TicketRow[]>(() => profile.data?.open_tickets || []);
const recentTickets = computed<TicketRow[]>(
  () => profile.data?.recent_tickets || []
);
const originals = computed<OriginalRow[]>(() => profile.data?.originals || []);

const showEdit = ref(false);
const form = reactive({
  key_account: false,
  default_product: "",
  proof_required: false,
  delivery_default: "",
  desk_notes: "",
});

function openEdit() {
  const c = customer.value || {};
  form.key_account = Boolean(c.key_account);
  form.default_product = c.default_product || "";
  form.proof_required = Boolean(c.proof_required);
  form.delivery_default = c.delivery_default || "";
  form.desk_notes = c.desk_notes || "";
  showEdit.value = true;
}

const save = createResource({
  url: "frappe.client.set_value",
  makeParams: () => ({
    doctype: "HD Customer",
    name: customer.value?.name || customer.value?.customer_name,
    fieldname: {
      key_account: form.key_account ? 1 : 0,
      default_product: form.default_product,
      proof_required: form.proof_required ? 1 : 0,
      delivery_default: form.delivery_default,
      desk_notes: form.desk_notes,
    },
  }),
  onSuccess() {
    showEdit.value = false;
    profile.fetch();
    toast.success(__("Kundprofilen är sparad"));
  },
  onError(err: any) {
    toast.error(err?.messages?.[0] || __("Kundprofilen kunde inte sparas"));
  },
});
</script>
