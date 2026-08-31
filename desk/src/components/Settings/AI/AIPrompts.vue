<template>
  <SettingsLayoutBase
    :title="__('AI Prompts')"
    :description="
      __(
        'Tune what each AI call is told. Every call reads its own prompt, and the shared fragment is prepended to all of them.'
      )
    "
  >
    <template #content>
      <!-- Loading State -->
      <div v-if="prompts.loading && !prompts.data" class="flex mt-28 w-full h-full">
        <Button :loading="true" variant="ghost" class="w-full" size="2xl" />
      </div>
      <!-- List -->
      <div v-else class="w-full h-full -ms-2">
        <div class="grid grid-cols-12 items-center gap-3 text-sm text-ink-gray-5 ms-2">
          <div class="col-span-5">{{ __("Call") }}</div>
          <div class="col-span-5">{{ __("Purpose") }}</div>
          <div class="col-span-2">{{ __("Version") }}</div>
        </div>
        <hr class="mx-2 mt-2" />
        <div v-for="(prompt, index) in ordered" :key="prompt.prompt_name">
          <div
            class="grid grid-cols-12 items-center gap-3 h-12.5 cursor-pointer hover:bg-surface-sidebar rounded"
            @click="openDialog(prompt)"
          >
            <div class="col-span-5 flex items-center gap-1.5 ps-2 min-w-0">
              <span class="text-base-medium text-ink-gray-7 truncate">
                {{ prompt.call || prompt.prompt_name }}
              </span>
              <Badge v-if="prompt.shared" theme="blue" :label="__('Every call')" />
              <Badge v-if="prompt.customised" theme="green" :label="__('Tuned')" />
              <Badge
                v-if="prompt.released && !prompt.enabled"
                :label="__('Disabled')"
              />
            </div>
            <div class="col-span-5 text-sm text-ink-gray-7 truncate">
              {{ prompt.purpose }}
            </div>
            <div class="col-span-2 text-sm text-ink-gray-7 truncate pe-2">
              <!-- Built-in wording carries no version, because nothing released it. -->
              {{ prompt.version || __("Built-in") }}
            </div>
          </div>
          <hr v-if="index !== ordered.length - 1" class="mx-2" />
        </div>
      </div>
    </template>
  </SettingsLayoutBase>

  <Dialog
    v-model:open="showDialog"
    :title="editing?.call || editing?.prompt_name"
    :actions="[
      {
        label: __('Save'),
        variant: 'solid',
        loading: savePrompt.loading,
        onClick: () => savePrompt.submit(),
      },
    ]"
  >
    <template #default>
      <form class="flex flex-col gap-4" @submit.prevent="savePrompt.submit()">
        <p class="text-sm text-ink-gray-6">{{ editing?.purpose }}</p>
        <!-- Two different retreats, and both are worth having. Switching a prompt
             off falls back to the built-in wording while keeping what was
             written here; resetting overwrites it and takes a new version. The
             shared fragment ships off, because turning it on changes how every
             generation records the version it came from. -->
        <FormControl
          v-model="draft.enabled"
          type="checkbox"
          :label="
            editing?.shared
              ? __('Prepend this to every AI call')
              : __('Use this wording instead of the built-in one')
          "
        />
        <FormControl
          v-model="draft.prompt"
          type="textarea"
          :rows="12"
          :label="__('Instructions')"
        />
        <div class="flex items-center justify-between">
          <span class="text-sm text-ink-gray-5">
            {{
              editing?.customised
                ? __("Tuned here. Version {0}.").replace("{0}", editing?.version)
                : __("This is the wording Helpdesk ships with.")
            }}
          </span>
          <Button
            v-if="editing?.customised"
            :label="__('Reset')"
            theme="gray"
            variant="subtle"
            :loading="resetPrompt.loading"
            @click="resetPrompt.submit()"
          />
        </div>
        <ErrorMessage :message="savePrompt.error || resetPrompt.error" />
      </form>
    </template>
  </Dialog>
</template>

<script setup lang="ts">
import SettingsLayoutBase from "@/components/layouts/SettingsLayoutBase.vue";
import { __ } from "@/translation";
import {
  Badge,
  Button,
  Dialog,
  ErrorMessage,
  FormControl,
  createResource,
  toast,
} from "frappe-ui";
import { computed, reactive, ref } from "vue";

const showDialog = ref(false);
const editing = ref(null);
const draft = reactive({ prompt: "", enabled: false });

const prompts = createResource({
  url: "helpdesk.api.governance.list_ai_prompts",
  auto: true,
});

// The fragment that governs every call belongs at the top, above the calls it
// governs; a prompt released under a name no call asks for any more goes last.
const ordered = computed(() => {
  const rows = prompts.data || [];
  return [
    ...rows.filter((row) => row.shared),
    ...rows.filter((row) => !row.shared && row.call),
    ...rows.filter((row) => !row.shared && !row.call),
  ];
});

function openDialog(prompt) {
  editing.value = prompt;
  draft.prompt = prompt.prompt;
  draft.enabled = Boolean(prompt.enabled);
  showDialog.value = true;
}

const savePrompt = createResource({
  url: "helpdesk.api.governance.upsert_ai_prompt",
  makeParams: () => ({
    prompt_name: editing.value.prompt_name,
    prompt: draft.prompt,
  }),
  onSuccess: async () => {
    // Enabling is its own change, logged separately from the wording.
    if (draft.enabled !== Boolean(editing.value.enabled)) {
      await setEnabled.submit();
    }
    showDialog.value = false;
    prompts.reload();
    toast.success(__("Prompt saved"));
  },
});

const setEnabled = createResource({
  url: "helpdesk.api.governance.set_ai_prompt_enabled",
  makeParams: () => ({
    prompt_name: editing.value.prompt_name,
    enabled: draft.enabled ? 1 : 0,
  }),
});

const resetPrompt = createResource({
  url: "helpdesk.api.governance.reset_ai_prompt",
  makeParams: () => ({ prompt_name: editing.value.prompt_name }),
  onSuccess: (prompt) => {
    draft.prompt = prompt.prompt;
    editing.value = prompt;
    prompts.reload();
    toast.success(__("Prompt reset to the built-in wording"));
  },
});
</script>
