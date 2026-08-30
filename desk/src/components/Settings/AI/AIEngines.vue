<template>
  <SettingsLayoutBase
    :title="__('AI Engines')"
    :description="
      __(
        'Configure the AI providers Helpdesk exports to the raphain registry document.'
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
      <div v-if="engines.loading" class="flex mt-28 w-full h-full">
        <Button :loading="true" variant="ghost" class="w-full" size="2xl" />
      </div>
      <!-- Empty State -->
      <EmptyState
        v-else-if="!engines.data?.length"
        variant="badge"
        :icon="SparklesIcon"
        :title="__('No AI engines found')"
        :description="__('Add one to get started.')"
      />
      <!-- List -->
      <div v-else class="w-full h-full -ms-2">
        <div class="grid grid-cols-12 items-center gap-3 text-sm text-ink-gray-5 ms-2">
          <div class="col-span-4">{{ __("Engine") }}</div>
          <div class="col-span-3">{{ __("Kind") }}</div>
          <div class="col-span-5">{{ __("Model") }}</div>
        </div>
        <hr class="mx-2 mt-2" />
        <div v-for="(engine, index) in engines.data" :key="engine.name">
          <div
            class="grid grid-cols-12 items-center gap-3 h-12.5 cursor-pointer hover:bg-surface-sidebar rounded"
            @click="openDialog(engine)"
          >
            <div class="col-span-4 flex items-center gap-1.5 ps-2 min-w-0">
              <span class="text-base-medium text-ink-gray-7 truncate">
                {{ engine.engine_name }}
              </span>
              <Badge v-if="engine.is_default" :label="__('Default')" />
              <Badge v-if="!engine.enabled" :label="__('Disabled')" />
            </div>
            <div class="col-span-3 text-sm text-ink-gray-7 truncate">
              {{ engine.kind }}
            </div>
            <div class="col-span-5 text-sm text-ink-gray-7 truncate pe-2">
              {{ engine.model }}
            </div>
          </div>
          <hr v-if="index !== engines.data.length - 1" class="mx-2" />
        </div>
      </div>
    </template>
  </SettingsLayoutBase>
  <Dialog
    v-model:open="showDialog"
    :title="isNew ? __('New AI engine') : __('Edit AI engine')"
    :actions="[
      {
        label: isNew ? __('Create') : __('Save'),
        variant: 'solid',
        loading: saveEngine.loading,
        onClick: () => saveEngine.submit(),
      },
    ]"
  >
    <template #default>
      <form class="flex flex-col gap-4" @submit.prevent="saveEngine.submit()">
        <FormControl
          v-model="engine.engine_name"
          type="text"
          :label="__('Name')"
          :placeholder="__('openai-primary')"
          :disabled="!isNew"
        />
        <FormControl
          v-model="engine.kind"
          type="select"
          :label="__('Kind')"
          :options="kindOptions"
        />
        <FormControl
          v-model="engine.model"
          type="text"
          :label="__('Model')"
          :placeholder="__('gpt-4o')"
        />
        <FormControl
          v-model="engine.base_url"
          type="text"
          :label="__('Base URL')"
          :placeholder="__('https://api.openai.com/v1')"
        />
        <FormControl
          v-model="engine.auth_type"
          type="select"
          :label="__('Authentication')"
          :options="authTypeOptions"
        />
        <template v-if="isKeyedAuth">
          <FormControl
            v-model="engine.auth_env"
            type="text"
            :label="__('Environment variable')"
            :placeholder="__('OPENAI_API_KEY')"
            :description="
              __(
                'The secret is read from this environment variable at run time.'
              )
            "
          />
          <FormControl
            v-model="engine.auth_secret"
            type="password"
            :label="__('Secret')"
            :placeholder="__('sk-...')"
            :description="
              __(
                'Stored on the site instead of an environment variable. Give one or the other, never both.'
              )
            "
          />
        </template>
        <template v-if="isOAuth">
          <FormControl
            v-model="engine.auth_access_token_env"
            type="text"
            :label="__('Access token environment variable')"
            :placeholder="__('OPENAI_OAUTH_TOKEN')"
            :description="
              __(
                'The access token is read from this environment variable at run time.'
              )
            "
          />
          <FormControl
            v-model="engine.auth_access_token"
            type="password"
            :label="__('Access token')"
            :placeholder="__('ya29....')"
            :description="
              __(
                'Stored on the site instead of an environment variable. Give one or the other, never both.'
              )
            "
          />
          <FormControl
            v-model="engine.auth_refresh_token_env"
            type="text"
            :label="__('Refresh token environment variable')"
            :placeholder="__('OPENAI_OAUTH_REFRESH')"
          />
          <FormControl
            v-model="engine.auth_expires_at_unix"
            type="number"
            :label="__('Access token expires at (unix)')"
            :placeholder="__('1735689600')"
            :description="__('Leave empty when the token does not expire.')"
          />
        </template>
        <FormControl
          v-model="engine.enabled"
          type="checkbox"
          :label="__('Enabled')"
        />
        <FormControl
          v-model="engine.is_default"
          type="checkbox"
          :label="__('Default engine')"
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
import { computed, ref } from "vue";
import SparklesIcon from "~icons/lucide/sparkles";

const kindOptions = [
  "openai",
  "openai_compatible",
  "openai_responses",
  "responses",
  "anthropic",
];

const authTypeOptions = ["none", "api_key", "bearer", "oauth"];

const KEYED_AUTH_TYPES = ["api_key", "bearer"];

const emptyEngine = () => ({
  engine_name: "",
  kind: "openai",
  model: "",
  base_url: "",
  auth_type: "none",
  auth_env: "",
  auth_secret: "",
  auth_access_token_env: "",
  auth_access_token: "",
  auth_refresh_token_env: "",
  auth_expires_at_unix: "",
  enabled: true,
  is_default: false,
});

const showDialog = ref(false);
const isNew = ref(true);
const engine = ref(emptyEngine());
const saveError = ref("");

// raphain shapes OAuth differently from api_key/bearer: it carries token
// references rather than a single secret, so the two sets never mix.
const isKeyedAuth = computed(() =>
  KEYED_AUTH_TYPES.includes(engine.value.auth_type)
);
const isOAuth = computed(() => engine.value.auth_type === "oauth");

const engines = createListResource({
  doctype: "HD AI Engine",
  fields: [
    "name",
    "engine_name",
    "kind",
    "model",
    "base_url",
    "auth_type",
    "auth_env",
    "auth_access_token_env",
    "auth_refresh_token_env",
    "auth_expires_at_unix",
    "is_default",
    "enabled",
  ],
  auto: true,
  orderBy: "modified desc",
  start: 0,
  pageLength: 99,
});

function openDialog(row?: Record<string, any>) {
  isNew.value = !row;
  engine.value = row
    ? {
        engine_name: row.engine_name,
        kind: row.kind,
        model: row.model,
        base_url: row.base_url || "",
        auth_type: row.auth_type || "none",
        auth_env: row.auth_env || "",
        // Secrets are never listed back, so they stay blank and are only sent
        // when the administrator types a new one.
        auth_secret: "",
        auth_access_token_env: row.auth_access_token_env || "",
        auth_access_token: "",
        auth_refresh_token_env: row.auth_refresh_token_env || "",
        auth_expires_at_unix: row.auth_expires_at_unix || "",
        enabled: Boolean(row.enabled),
        is_default: Boolean(row.is_default),
      }
    : emptyEngine();
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

const saveEngine = createResource({
  url: "helpdesk.api.ai_engine.upsert_engine",
  makeParams() {
    const values = engine.value;
    const params: Record<string, any> = {
      engine_name: values.engine_name,
      kind: values.kind,
      model: values.model,
      base_url: values.base_url,
      auth_type: values.auth_type,
      enabled: values.enabled ? 1 : 0,
      is_default: values.is_default ? 1 : 0,
    };
    // Whichever set does not belong to the chosen type is sent empty, so a
    // switched engine never keeps a field its auth variant forbids. A blank
    // secret box on an existing engine means "leave the stored one alone", so
    // those keys are only sent once something has been typed.
    if (isKeyedAuth.value) {
      params.auth_env = values.auth_env;
      if (values.auth_secret) params.auth_secret = values.auth_secret;
    } else {
      params.auth_env = "";
      params.auth_secret = "";
    }
    if (isOAuth.value) {
      params.auth_access_token_env = values.auth_access_token_env;
      params.auth_refresh_token_env = values.auth_refresh_token_env;
      params.auth_expires_at_unix = Number(values.auth_expires_at_unix) || 0;
      if (values.auth_access_token)
        params.auth_access_token = values.auth_access_token;
    } else {
      params.auth_access_token_env = "";
      params.auth_access_token = "";
      params.auth_refresh_token_env = "";
      params.auth_expires_at_unix = 0;
    }
    return params;
  },
  validate(params) {
    if (!params.engine_name) return __("Name is required");
    if (!params.model) return __("Model is required");
  },
  auto: false,
  onSuccess() {
    toast.success(isNew.value ? __("AI engine created") : __("AI engine updated"));
    saveError.value = "";
    showDialog.value = false;
    engines.reload();
  },
  onError(error) {
    // The dialog stays open: the reason is on screen and the typed values are
    // still there to correct.
    saveError.value = errorReason(error, __("Could not save the AI engine"));
    toast.error(saveError.value);
  },
});
</script>
