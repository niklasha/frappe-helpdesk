<template>
  <!-- S/5 + S/7: the work material ("Arbetsunderlag") — the files the ticket
       carries, classified by helpdesk.api.ticket_files. Strip mode sits under
       the order card on the agent page; full mode is the Filer tab. The
       component takes no room when the inventory errors or is empty in strip
       mode, and says so in full mode. -->
  <div v-if="mode === 'full'" class="flex-1 overflow-y-auto px-5 py-3 text-sm">
    <p class="mb-2 text-base-medium text-ink-gray-8">
      {{ __("Filer") }} · {{ __("{0} relevanta filer", [relevant.length]) }}
    </p>
    <p v-if="!files.length" class="text-ink-gray-5">
      {{ __("Inga filer på ärendet") }}
    </p>
    <div v-else class="overflow-x-auto">
      <table class="w-full text-left">
        <thead class="text-xs text-ink-gray-5">
          <tr>
            <th class="py-1 pr-3 font-normal">{{ __("Fil") }}</th>
            <th class="py-1 pr-3 font-normal">{{ __("Format") }}</th>
            <th class="py-1 pr-3 font-normal">{{ __("Slag") }}</th>
            <th class="py-1 pr-3 font-normal">{{ __("Storlek/sidor") }}</th>
            <th class="py-1 pr-3 font-normal">{{ __("Källa") }}</th>
            <th class="py-1 font-normal">{{ __("Bedömning") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="file in files"
            :key="file.file_url || file.file_name"
            class="border-t align-top"
          >
            <td class="py-1.5 pr-3">
              <a
                :href="file.file_url"
                target="_blank"
                rel="noopener"
                class="flex items-center gap-1.5 text-ink-gray-8 hover:underline"
              >
                <component :is="iconOf(file)" class="h-4 w-4 shrink-0" />
                <span class="truncate">{{ file.file_name }}</span>
              </a>
            </td>
            <td class="py-1.5 pr-3 uppercase text-ink-gray-7">
              {{ file.format || "–" }}
            </td>
            <td class="py-1.5 pr-3">
              <Badge :theme="themeOf(file)" :label="file.kind || __('Övrigt')" />
            </td>
            <td class="py-1.5 pr-3 text-ink-gray-7">{{ sizeOf(file) }}</td>
            <td class="py-1.5 pr-3 text-ink-gray-7">{{ sourceOf(file) }}</td>
            <td class="py-1.5 text-ink-gray-7">
              <span>{{ file.assessment || "–" }}</span>
              <span
                v-if="file.assessment && file.assessed_by === 'model'"
                class="ml-1 text-xs text-ink-gray-5"
              >
                ({{ __("AI") }})
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div v-else-if="files.length" class="border-b px-5 py-3 text-sm">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-base-medium text-ink-gray-8">
        {{ __("Arbetsunderlag") }}
      </span>
      <span class="text-ink-gray-5">
        · {{ __("{0} relevanta filer", [relevant.length]) }}
      </span>
      <Button
        v-if="files.length > relevant.length"
        class="ml-auto"
        size="sm"
        variant="ghost"
        :label="showAll ? __('Visa relevanta') : __('Visa alla')"
        @click="showAll = !showAll"
      />
    </div>
    <div class="mt-2 flex flex-wrap gap-2">
      <Tooltip
        v-for="file in shown"
        :key="file.file_url || file.file_name"
        :text="file.assessment || file.kind || ''"
      >
        <a
          :href="file.file_url"
          target="_blank"
          rel="noopener"
          class="flex max-w-xs items-center gap-1.5 rounded border px-2 py-1 text-ink-gray-8 hover:bg-surface-gray-2"
        >
          <component :is="iconOf(file)" class="h-4 w-4 shrink-0 text-ink-gray-6" />
          <span class="truncate">{{ file.file_name }}</span>
          <Badge :theme="themeOf(file)" :label="file.kind || __('Övrigt')" />
        </a>
      </Tooltip>
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { TicketSymbol } from "@/types";
import { Badge, Button, Tooltip, createResource } from "frappe-ui";
import { computed, inject, ref, watch } from "vue";
import LucideFileImage from "~icons/lucide/file-image";
import LucideFileText from "~icons/lucide/file-text";
import LucideFile from "~icons/lucide/file";
import LucidePenTool from "~icons/lucide/pen-tool";

interface TicketFile {
  file_name: string;
  file_url?: string;
  format?: string;
  kind?: string;
  vector?: number;
  relevance?: string;
  source?: string;
  assessment?: string | null;
  assessed_by?: string | null;
  file_size?: number | null;
  pages?: number | null;
}

const props = withDefaults(defineProps<{ mode?: "strip" | "full" }>(), {
  mode: "strip",
});

const ticket = inject(TicketSymbol)!;

// Same no-auto + watch pattern as TicketOrderCard: the ticket may not be
// loaded at mount. An error leaves `data` empty, so the strip hides itself.
const inventory = createResource({
  url: "helpdesk.api.ticket_files.ticket_files",
  makeParams: () => ({ ticket_id: ticket.value?.doc?.name }),
  onError() {},
});

watch(
  () => ticket.value?.doc?.name,
  (name) => {
    if (name) inventory.fetch();
  },
  { immediate: true }
);

const files = computed<TicketFile[]>(() =>
  Array.isArray(inventory.data) ? inventory.data : []
);
const relevant = computed(() =>
  files.value.filter((f) => f.relevance !== "Övrigt")
);
const showAll = ref(false);
const shown = computed(() => (showAll.value ? files.value : relevant.value));

function iconOf(file: TicketFile) {
  if (file.kind === "Vektor") return LucidePenTool;
  if (file.kind === "Raster") return LucideFileImage;
  if (file.kind === "Dokument") return LucideFileText;
  return LucideFile;
}

function themeOf(file: TicketFile): "green" | "orange" | "blue" | "gray" {
  if (file.kind === "Vektor") return "green";
  if (file.kind === "Raster") return "orange";
  if (file.kind === "Dokument") return "blue";
  return "gray";
}

function sourceOf(file: TicketFile): string {
  return file.source === "message" ? __("Meddelande") : __("Ärende");
}

function sizeOf(file: TicketFile): string {
  const parts: string[] = [];
  if (file.file_size) {
    const kb = file.file_size / 1024;
    parts.push(kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.ceil(kb)} kB`);
  }
  if (file.pages) parts.push(__("{0} sidor", [file.pages]));
  return parts.join(" · ") || "–";
}
</script>
