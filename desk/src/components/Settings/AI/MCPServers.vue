<template>
  <SettingsLayoutBase
    :title="__('MCP Servers')"
    :description="
      __(
        'Configure the stdio MCP servers Helpdesk exports as dispatch configuration.'
      )
    "
  >
    <template #header-actions>
      <Button
        :label="__('New')"
        class="rtl:flex-row-reverse"
        theme="gray"
        variant="solid"
        icon-left="lucide-plus"
        @click="openDialog()"
      />
    </template>
    <template #content>
      <!-- Loading State -->
      <div v-if="servers.loading" class="flex mt-28 w-full h-full">
        <Button :loading="true" variant="ghost" class="w-full" size="2xl" />
      </div>
      <!-- Empty State -->
      <EmptyState
        v-else-if="!servers.data?.length"
        variant="badge"
        :icon="ServerIcon"
        :title="__('No MCP servers found')"
        :description="__('Add one to get started.')"
      />
      <!-- List -->
      <div v-else class="w-full h-full -ms-2">
        <div class="grid grid-cols-12 items-center gap-3 text-sm text-ink-gray-5 ms-2">
          <div class="col-span-4">{{ __("Server") }}</div>
          <div class="col-span-5">{{ __("Command") }}</div>
          <div class="col-span-3">{{ __("Protocol") }}</div>
        </div>
        <hr class="mx-2 mt-2" />
        <div v-for="(server, index) in servers.data" :key="server.name">
          <div
            class="grid grid-cols-12 items-center gap-3 h-12.5 cursor-pointer hover:bg-surface-sidebar rounded"
            @click="openDialog(server)"
          >
            <div class="col-span-4 flex items-center gap-1.5 ps-2 min-w-0">
              <span class="text-base-medium text-ink-gray-7 truncate">
                {{ server.server_name }}
              </span>
              <Badge v-if="!server.enabled" :label="__('Disabled')" />
            </div>
            <div class="col-span-5 text-sm text-ink-gray-7 truncate">
              {{ server.command }}
            </div>
            <div class="col-span-3 text-sm text-ink-gray-7 truncate pe-2">
              {{ server.protocol_version }}
            </div>
          </div>
          <hr v-if="index !== servers.data.length - 1" class="mx-2" />
        </div>
      </div>
    </template>
  </SettingsLayoutBase>
  <Dialog
    v-model:open="showDialog"
    :title="isNew ? __('New MCP server') : __('Edit MCP server')"
    :actions="[
      {
        label: isNew ? __('Create') : __('Save'),
        variant: 'solid',
        loading: saveServer.loading,
        onClick: () => saveServer.submit(),
      },
    ]"
  >
    <template #default>
      <form class="flex flex-col gap-4" @submit.prevent="saveServer.submit()">
        <FormControl
          v-model="server.server_name"
          type="text"
          :label="__('Name')"
          :placeholder="__('imap')"
          :disabled="!isNew"
        />
        <FormControl
          v-model="server.command"
          type="text"
          :label="__('Command')"
          :placeholder="__('imap-mcp-server')"
        />
        <FormControl
          v-model="server.arguments"
          type="textarea"
          :label="__('Arguments')"
          :placeholder="__('--config\n/etc/imap-mcp.toml')"
          :description="__('One argument per line.')"
        />
        <FormControl
          v-model="server.protocol_version"
          type="text"
          :label="__('Protocol version')"
          :placeholder="__('2025-06-18')"
        />
        <div class="grid grid-cols-2 gap-4">
          <FormControl
            v-model="server.handshake_timeout"
            type="number"
            :label="__('Handshake timeout (s)')"
          />
          <FormControl
            v-model="server.call_timeout"
            type="number"
            :label="__('Call timeout (s)')"
          />
        </div>
        <FormControl
          v-model="server.enabled"
          type="checkbox"
          :label="__('Enabled')"
        />
        <ErrorMessage :message="saveError" />
      </form>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import EmptyState from "@/components/EmptyState.vue";
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";
import { __ } from "@/translation";
import {
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  FormControl,
  createListResource,
  createResource,
  toast,
} from "frappe-ui";
import { ref } from "vue";
import ServerIcon from "~icons/lucide/server";

const emptyServer = () => ({
  server_name: "",
  command: "",
  arguments: "",
  protocol_version: "2025-06-18",
  handshake_timeout: 15,
  call_timeout: 60,
  enabled: true,
});

const showDialog = ref(false);
const isNew = ref(true);
const server = ref(emptyServer());
const saveError = ref("");

const servers = createListResource({
  doctype: "HD AI MCP Server",
  fields: [
    "name",
    "server_name",
    "command",
    "arguments",
    "protocol_version",
    "handshake_timeout",
    "call_timeout",
    "enabled",
  ],
  auto: true,
  orderBy: "modified desc",
  start: 0,
  pageLength: 99,
});

function parseArguments(value: unknown): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map((item) => String(item));
  try {
    const parsed = JSON.parse(String(value));
    if (Array.isArray(parsed)) return parsed.map((item) => String(item));
  } catch {
    // Not JSON: fall through to the one-argument-per-line form.
  }
  return String(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function openDialog(row?: Record<string, any>) {
  isNew.value = !row;
  server.value = row
    ? {
        server_name: row.server_name,
        command: row.command,
        arguments: parseArguments(row.arguments).join("\n"),
        protocol_version: row.protocol_version || "2025-06-18",
        handshake_timeout: row.handshake_timeout ?? 15,
        call_timeout: row.call_timeout ?? 60,
        enabled: Boolean(row.enabled),
      }
    : emptyServer();
  saveError.value = "";
  showDialog.value = true;
}

/**
 * The reason the server actually gave.
 *
 * A clean validation arrives as `messages`; anything raised before the method
 * runs — a type check on the arguments, for instance — arrives as a plain
 * exception with `exc_type` and `message`. Falling straight through to a
 * generic string hides exactly the cases worth reading.
 */
function errorReason(error: any, fallback: string): string {
  const message = error?.messages?.[0];
  if (message) return message;
  if (error?.exc_type) {
    return error.message
      ? `${error.exc_type}: ${error.message}`
      : error.exc_type;
  }
  return error?.message || fallback;
}

const saveServer = createResource({
  url: "helpdesk.api.mcp_server.upsert_mcp_server",
  makeParams() {
    const values = server.value;
    return {
      server_name: values.server_name,
      command: values.command,
      arguments: parseArguments(values.arguments),
      protocol_version: values.protocol_version,
      handshake_timeout: Number(values.handshake_timeout),
      call_timeout: Number(values.call_timeout),
      enabled: values.enabled ? 1 : 0,
    };
  },
  validate(params) {
    if (!params.server_name) return __("Name is required");
    if (!params.command) return __("Command is required");
    if (!(params.handshake_timeout > 0) || !(params.call_timeout > 0))
      return __("Timeouts must be greater than zero");
  },
  auto: false,
  onSuccess() {
    toast.success(
      isNew.value ? __("MCP server created") : __("MCP server updated")
    );
    saveError.value = "";
    showDialog.value = false;
    servers.reload();
  },
  onError(error) {
    // The dialog stays open: the reason is on screen and the typed values are
    // still there to correct.
    saveError.value = errorReason(error, __("Could not save the MCP server"));
    toast.error(saveError.value);
  },
});
</script>
