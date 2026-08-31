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
        <!-- The provider names the dialect its backend speaks, and raphain maps
             kind straight to a wire path: choosing the wrong one posts to an
             endpoint that does not exist. -->
        <FormControl
          v-if="providerKind"
          :model-value="providerKind"
          type="text"
          disabled
          :label="__('Kind')"
          :description="
            __('The dialect {0} speaks.').replace('{0}', engine.auth_oauth_provider)
          "
        />
        <FormControl
          v-else
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
        <!-- A provider whose preset names its own backend owns this value: the
             Codex bearer is refused by api.openai.com, so offering that address
             here only builds a configuration the engine will reject on save. -->
        <FormControl
          v-if="providerBaseUrl"
          :model-value="providerBaseUrl"
          type="text"
          disabled
          :label="__('Base URL')"
          :description="
            __('Set by {0}, which is the only backend its token is accepted by.').replace(
              '{0}',
              engine.auth_oauth_provider
            )
          "
        />
        <FormControl
          v-else
          v-model="engine.base_url"
          type="text"
          :label="__('Base URL')"
          :placeholder="__('Leave empty unless the provider needs a specific host')"
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
            autocomplete="new-password"
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
            v-model="engine.auth_oauth_provider"
            type="select"
            :label="__('OAuth provider')"
            :options="providerOptions"
            :description="
              __(
                'Bound to a provider, Helpdesk obtains the token itself and keeps it refreshed.'
              )
            "
          />
          <!-- Connecting spends a grant against the saved engine, so it only
               opens once the binding on screen is the binding on the site. -->
          <div
            v-if="canConnect"
            class="flex flex-col gap-3 rounded border border-outline-gray-2 p-3"
          >
            <div class="flex items-center justify-between">
              <span class="text-base-medium text-ink-gray-7">
                {{ __("Connection") }}
              </span>
              <Badge :label="statusLabel" :theme="statusTheme" />
            </div>
            <div
              v-if="connection.data?.account_id"
              class="text-p-sm text-ink-gray-6"
            >
              {{ __("Account") }}: {{ connection.data.account_id }}
            </div>
            <div v-if="expiresAt" class="text-p-sm text-ink-gray-6">
              {{ __("Access token expires") }}: {{ expiresAt }}
            </div>
            <FormControl
              v-if="offeredModes.length > 1"
              v-model="mode"
              type="select"
              :label="__('How to authorize')"
              :options="offeredModes"
            />
            <!-- Every button here is type="button": inside the form, a button
                 with no type submits it, and connecting is not saving. -->
            <div class="flex gap-2">
              <Button
                type="button"
                :label="isConnected ? __('Reconnect') : __('Connect')"
                theme="gray"
                variant="subtle"
                :loading="beginAuthorization.loading"
                @click="beginAuthorization.submit()"
              />
              <Button
                v-if="isConnected"
                type="button"
                :label="__('Disconnect')"
                theme="gray"
                variant="ghost"
                :loading="disconnectEngine.loading"
                @click="disconnectEngine.submit()"
              />
            </div>
            <template v-if="pending">
              <a
                v-if="pending.authorize_url"
                class="text-p-sm text-ink-blue-3 underline w-fit"
                :href="pending.authorize_url"
                target="_blank"
                rel="noopener"
              >
                {{ __("Open the approval page again") }}
              </a>
              <!-- The provider sends the browser back to this site, which
                   finishes the grant server-side; nothing is left to type. -->
              <template v-if="pending.mode === 'redirect'">
                <div class="text-p-sm text-ink-gray-6">
                  {{
                    __(
                      "Approve in the tab that opened. Helpdesk stores the token when the provider sends the browser back."
                    )
                  }}
                </div>
                <Button
                  type="button"
                  :label="__('Check connection')"
                  theme="gray"
                  variant="subtle"
                  :loading="connection.loading"
                  @click="checkConnection()"
                />
              </template>
              <!-- The Codex client redirects to a port on the administrator's
                   own machine, so the address bar is the only way back. -->
              <template v-else-if="pending.mode === 'loopback_paste'">
                <FormControl
                  v-model="landedUrl"
                  type="text"
                  :label="__('Address the browser landed on')"
                  :placeholder="__('http://localhost:1455/auth/callback?code=...')"
                  :description="
                    __(
                      'Approve in the tab that opened, then copy the whole address here, even if that page failed to load.'
                    )
                  "
                />
                <Button
                  type="button"
                  :label="__('Complete connection')"
                  theme="gray"
                  variant="subtle"
                  :loading="completeConnection.loading"
                  @click="completeConnection.submit()"
                />
              </template>
              <template v-else>
                <div class="text-p-sm text-ink-gray-6">
                  {{ __("Enter this code at") }}
                  <a
                    class="text-ink-blue-3 underline"
                    :href="pending.verification_uri"
                    target="_blank"
                    rel="noopener"
                    >{{ pending.verification_uri }}</a
                  >: <span class="font-mono">{{ pending.user_code }}</span>
                </div>
                <Button
                  type="button"
                  :label="__('I have approved it')"
                  theme="gray"
                  variant="subtle"
                  :loading="pollDevice.loading"
                  @click="pollDevice.submit()"
                />
              </template>
            </template>
            <ErrorMessage :message="connectError" />
          </div>
          <div
            v-else-if="engine.auth_oauth_provider"
            class="text-p-sm text-ink-gray-5"
          >
            {{ __("Save the engine to authorize it against this provider.") }}
          </div>
          <button
            type="button"
            class="text-p-sm text-ink-gray-6 underline w-fit"
            @click="showTokenFields = !showTokenFields"
          >
            {{
              showTokenFields
                ? __("Hide the token fields")
                : __("Set the tokens by hand")
            }}
          </button>
          <template v-if="showTokenFields">
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
              autocomplete="new-password"
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
            <!-- With a provider bound, the expiry is written by the grant and
                 by every refresh, so it is reported above rather than typed. -->
            <FormControl
              v-if="!engine.auth_oauth_provider"
              v-model="engine.auth_expires_at_unix"
              type="number"
              :label="__('Access token expires at (unix)')"
              :placeholder="__('1735689600')"
              :description="__('Leave empty when the token does not expire.')"
            />
          </template>
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

// The same three flags the server reads off the provider, in the same order it
// reports them, so a page and a refusal never disagree about what is on offer.
const MODE_FLAGS: Record<string, string> = {
  redirect: "supports_redirect",
  loopback_paste: "supports_loopback_paste",
  device_code: "supports_device_code",
};

// A refusal carries a machine tag for callers that switch on the reason. The
// person reading this dialog only needs the sentence in front of it.
const REFUSAL_TAG = /\s*\[hd-oauth:[a-z_]+\]\s*$/;

const emptyEngine = () => ({
  engine_name: "",
  kind: "openai",
  model: "",
  base_url: "",
  auth_type: "none",
  auth_env: "",
  auth_secret: "",
  auth_oauth_provider: "",
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
// The provider the engine is bound to on the site, which is not always the one
// on screen: a binding only picked in the dialog has nothing to authorize yet.
const boundTo = ref("");
const showTokenFields = ref(false);
const mode = ref("");
const pending = ref<Record<string, any> | null>(null);
const landedUrl = ref("");
const connectError = ref("");

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
    "auth_oauth_provider",
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

// The provider list comes from the OAuth module rather than the doctype,
// because that endpoint already leaves the client secret out of every row.
const oauthProviders = createResource({
  url: "helpdesk.api.ai_oauth.list_providers",
  auto: true,
});

const providerOptions = computed(() => [
  { label: __("None"), value: "" },
  ...(oauthProviders.data || []).map((row: Record<string, any>) => ({
    label: row.provider_name,
    value: row.provider_name,
  })),
]);

const chosenProvider = computed(() =>
  (oauthProviders.data || []).find(
    (row: Record<string, any>) => row.provider_name === engine.value.auth_oauth_provider
  )
);

// The backend the chosen provider authenticates for, when its preset names one.
// Empty for a generic OIDC provider, which leaves the field to the administrator.
const providerKind = computed(
  () => (isOAuth.value && chosenProvider.value?.engine_kind) || ""
);

const providerBaseUrl = computed(
  () => (isOAuth.value && chosenProvider.value?.engine_base_url) || ""
);

// begin_authorization refuses to guess when a provider offers more than one way
// in, so the page names the mode out of the flags it has already been given.
const offeredModes = computed(() =>
  Object.keys(MODE_FLAGS).filter((name) =>
    Boolean(chosenProvider.value?.[MODE_FLAGS[name]])
  )
);

const canConnect = computed(
  () => !isNew.value && Boolean(boundTo.value) && boundTo.value === engine.value.auth_oauth_provider
);

const connection = createResource({
  url: "helpdesk.api.ai_oauth.connection_status",
  makeParams: () => ({ engine_name: engine.value.engine_name }),
  auto: false,
});

const isConnected = computed(() =>
  ["connected", "expired"].includes(connection.data?.status)
);

const statusLabel = computed(() => {
  const status = connection.data?.status;
  if (status === "connected") return __("Connected");
  if (status === "expired") return __("Expired");
  return __("Not connected");
});

const statusTheme = computed(() => {
  const status = connection.data?.status;
  if (status === "connected") return "green";
  return status === "expired" ? "amber" : "gray";
});

const expiresAt = computed(() => {
  const seconds = connection.data?.expires_at_unix;
  return seconds ? new Date(seconds * 1000).toLocaleString() : "";
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
        auth_oauth_provider: row.auth_oauth_provider || "",
        auth_access_token_env: row.auth_access_token_env || "",
        auth_access_token: "",
        auth_refresh_token_env: row.auth_refresh_token_env || "",
        auth_expires_at_unix: row.auth_expires_at_unix || "",
        enabled: Boolean(row.enabled),
        is_default: Boolean(row.is_default),
      }
    : emptyEngine();
  saveError.value = "";
  boundTo.value = engine.value.auth_oauth_provider;
  // An engine that obtains its own token has no use for the token boxes, so
  // they start folded away and stay one click from an administrator who needs
  // them anyway.
  showTokenFields.value = !boundTo.value;
  forgetGrant();
  mode.value = "";
  connection.data = null;
  if (canConnect.value) connection.fetch();
  showDialog.value = true;
}

/** Drop whatever is left of a half-finished authorization. */
function forgetGrant() {
  pending.value = null;
  landedUrl.value = "";
  connectError.value = "";
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
      // A locked field shows the provider's value while the model still holds
      // whatever was chosen before the provider was picked, so send what is on
      // screen rather than what was left behind.
      kind: providerKind.value || values.kind,
      model: values.model,
      base_url: providerBaseUrl.value ? "" : values.base_url,
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
      params.auth_oauth_provider = values.auth_oauth_provider;
      params.auth_access_token_env = values.auth_access_token_env;
      params.auth_refresh_token_env = values.auth_refresh_token_env;
      if (values.auth_access_token)
        params.auth_access_token = values.auth_access_token;
      // The expiry of a provider-bound engine is the grant's to write. Sending
      // back what the dialog was opened with would undo a refresh that ran
      // while it was open.
      if (!values.auth_oauth_provider)
        params.auth_expires_at_unix = Number(values.auth_expires_at_unix) || 0;
    } else {
      params.auth_oauth_provider = "";
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

/** The reason a refusal gave, without the tag it carries for machines. */
function refusalReason(error: any, fallback: string): string {
  return errorReason(error, fallback).replace(REFUSAL_TAG, "");
}

function checkConnection() {
  connection.fetch();
}

/** One grant has ended in a token: the panel now speaks for the engine again. */
function connectionMade() {
  forgetGrant();
  toast.success(__("AI engine connected"));
  connection.fetch();
}

const beginAuthorization = createResource({
  url: "helpdesk.api.ai_oauth.begin_authorization",
  makeParams: () => ({
    engine_name: engine.value.engine_name,
    // Named only when the provider offers a choice, so a provider with one way
    // in keeps working if its flags change under us.
    mode: offeredModes.value.length > 1 ? mode.value || offeredModes.value[0] : null,
  }),
  auto: false,
  onSuccess(data: Record<string, any>) {
    forgetGrant();
    pending.value = data;
    // Only the address the provider's own record built is ever opened: a URL
    // this page composed would be Helpdesk vouching for wherever it pointed.
    if (data.authorize_url) window.open(data.authorize_url, "_blank", "noopener");
  },
  onError(error) {
    pending.value = null;
    connectError.value = refusalReason(error, __("Could not start the authorization"));
  },
});

const completeConnection = createResource({
  url: "helpdesk.api.ai_oauth.complete_from_redirect_url",
  makeParams: () => ({ grant: pending.value?.grant, redirect_url: landedUrl.value }),
  validate(params) {
    if (!params.redirect_url) return __("Paste the address the browser landed on");
  },
  auto: false,
  onSuccess: connectionMade,
  onError(error) {
    // The grant is spent either way, so the panel goes back to offering a new
    // authorization rather than a second paste into a dead one.
    pending.value = null;
    connectError.value = refusalReason(error, __("Could not complete the authorization"));
  },
});

const pollDevice = createResource({
  url: "helpdesk.api.ai_oauth.poll_device_authorization",
  makeParams: () => ({ grant: pending.value?.grant }),
  auto: false,
  onSuccess(data: Record<string, any>) {
    if (data.status === "authorization_pending") {
      connectError.value = __("Nobody has approved this code yet. Try again in a moment.");
      return;
    }
    connectionMade();
  },
  onError(error) {
    pending.value = null;
    connectError.value = refusalReason(error, __("Could not complete the authorization"));
  },
});

const disconnectEngine = createResource({
  url: "helpdesk.api.ai_oauth.disconnect_engine",
  makeParams: () => ({ engine_name: engine.value.engine_name }),
  auto: false,
  onSuccess() {
    forgetGrant();
    toast.success(__("AI engine disconnected"));
    connection.fetch();
  },
  onError(error) {
    connectError.value = refusalReason(error, __("Could not disconnect the engine"));
  },
});
</script>
