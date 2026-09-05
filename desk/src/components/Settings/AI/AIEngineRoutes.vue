<template>
  <SettingsLayoutBase
    :title="__('Motorordning')"
    :description="
      __(
        'Ordningen AI-anrop frågar motorerna i. Den första som svarar vinner, och en motor vars kvot tagit slut faller igenom till nästa.'
      )
    "
  >
    <template #header-actions>
      <Button
        :label="__('Spara')"
        theme="gray"
        variant="solid"
        :loading="saveRoutes.loading"
        @click="saveRoutes.submit()"
      />
    </template>
    <template #content>
      <div v-if="routes.loading || engines.loading" class="flex mt-28 w-full h-full">
        <Button :loading="true" variant="ghost" class="w-full" size="2xl" />
      </div>
      <div v-else class="flex flex-col gap-6 w-full h-full">
        <!-- The global chain: the order every call uses unless it names its own. -->
        <section class="flex flex-col gap-2">
          <div class="text-base-medium text-ink-gray-8">
            {{ __("Global ordning") }}
          </div>
          <div class="text-sm text-ink-gray-5">
            {{
              __(
                "Gäller alla anrop som inte har en egen kedja. Tom lista betyder standardmotorn."
              )
            }}
          </div>
          <EngineChain
            :chain="globalChain"
            :options="engineNames"
            @update="globalChain = $event"
          />
        </section>

        <hr />

        <!-- Per call: an administrator can leave a call on the global order, or
             give it a chain of its own — a slow call and a cheap one do not have
             to share an allowance. -->
        <section class="flex flex-col gap-4">
          <div class="text-base-medium text-ink-gray-8">
            {{ __("Per anrop") }}
          </div>
          <div v-for="entry in CALLS" :key="entry.call" class="flex flex-col gap-2">
            <div class="flex items-center justify-between gap-3">
              <span class="text-base-medium text-ink-gray-7">
                {{ __(entry.label) }}
              </span>
              <FormControl
                type="select"
                :model-value="perCall[entry.call] ? 'own' : 'global'"
                :options="[
                  { label: __('Följer global ordning'), value: 'global' },
                  { label: __('Egen kedja'), value: 'own' },
                ]"
                @update:model-value="setMode(entry.call, $event)"
              />
            </div>
            <EngineChain
              v-if="perCall[entry.call]"
              :chain="perCall[entry.call]"
              :options="engineNames"
              @update="perCall[entry.call] = $event"
            />
          </div>
        </section>
        <ErrorMessage :message="saveRoutes.error" />
      </div>
    </template>
  </SettingsLayoutBase>
</template>

<script setup lang="ts">
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";
import { __ } from "@/translation";
import {
  Button,
  ErrorMessage,
  FormControl,
  createResource,
  toast,
} from "frappe-ui";
import { computed, reactive, ref } from "vue";
import EngineChain from "./EngineChain.vue";

// The reserved call name the backend reads as "every call".
const EVERY_CALL = "*";

// The calls ai_generation names, with labels an administrator recognises.
const CALLS = [
  { call: "ticket_triage", label: "Triage" },
  { call: "order_extraction", label: "Orderutdrag" },
  { call: "translation_inbound", label: "Inkommande översättning" },
  { call: "translation_outbound", label: "Utgående översättning" },
  { call: "message_translation", label: "Meddelandeöversättning" },
  { call: "knowledge_reply", label: "Kunskapssvar" },
  { call: "common_question", label: "Vanlig fråga" },
  { call: "completion_request", label: "Fritt svar" },
];

const globalChain = ref<string[]>([]);
// A call missing from this map, or holding null, follows the global order.
const perCall = reactive<Record<string, string[] | null>>({});
// Rows for a call this page has no section for: a route written by hand or
// by a later wave. set_engine_routes replaces the whole table, so they are
// remembered on load and sent back unchanged on save rather than being
// silently deleted because the page could not place them.
const unplaced = ref<Record<string, any>[]>([]);

const engines = createResource({
  url: "helpdesk.api.ai_engine.list_engines",
  auto: true,
});

const engineNames = computed(() =>
  (engines.data || []).map((row: Record<string, any>) => row.engine_name)
);

const routes = createResource({
  url: "helpdesk.api.ai_engine.engine_routes",
  auto: true,
  onSuccess(rows: Record<string, any>[]) {
    load(rows || []);
  },
});

/** Turn the stored rows back into one chain per call, priority ascending. */
function load(rows: Record<string, any>[]) {
  const byCall: Record<string, Record<string, any>[]> = {};
  for (const row of rows) {
    const call = row.call || EVERY_CALL;
    (byCall[call] = byCall[call] || []).push(row);
  }
  const chainFor = (call: string) =>
    (byCall[call] || [])
      .slice()
      .sort((a, b) => (a.priority || 0) - (b.priority || 0))
      .map((row) => row.engine);
  globalChain.value = chainFor(EVERY_CALL);
  for (const entry of CALLS) {
    perCall[entry.call] = byCall[entry.call] ? chainFor(entry.call) : null;
  }
  const modelled = new Set([EVERY_CALL, ...CALLS.map((entry) => entry.call)]);
  unplaced.value = Object.keys(byCall)
    .filter((call) => !modelled.has(call))
    .flatMap((call) =>
      byCall[call].map((row) => ({
        call,
        engine: row.engine,
        priority: row.priority || 0,
      }))
    );
}

function setMode(call: string, mode: string) {
  perCall[call] = mode === "own" ? (perCall[call] || []).slice() : null;
}

/** Flatten every chain into rows, numbering priority from 1 as stored. */
function rowsToSave() {
  const rows: Record<string, any>[] = [];
  const push = (call: string, chain: string[]) =>
    chain
      .filter(Boolean)
      .forEach((engine, index) =>
        rows.push({ call, engine, priority: index + 1 })
      );
  push(EVERY_CALL, globalChain.value);
  for (const entry of CALLS) {
    if (perCall[entry.call]) push(entry.call, perCall[entry.call] as string[]);
  }
  // The routes this page does not model go back exactly as they were read.
  return rows.concat(unplaced.value);
}

const saveRoutes = createResource({
  url: "helpdesk.api.ai_engine.set_engine_routes",
  makeParams: () => ({ routes: rowsToSave() }),
  onSuccess(rows: Record<string, any>[]) {
    load(rows || []);
    toast.success(__("Motorordningen är sparad"));
  },
});
</script>
