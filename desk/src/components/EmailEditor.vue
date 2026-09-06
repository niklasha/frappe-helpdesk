<template>
  <Editor
    ref="editorRef"
    v-model="newEmail"
    :placeholder="placeholder"
    :editable="editable"
    :extensions="extensions"
    :upload-function="(file:any)=>uploadFunction(file, doctype, ticketId)"
  >
    <template #default>
      <div @keydown.capture="handleKeydown">
        <!-- Email headers -->
        <div
          v-if="hasMultipleSenders"
          class="mx-6 md:mx-5 flex items-center gap-2 border-t py-2.5 h-12.5"
        >
          <span class="text-p-xs text-ink-gray-4">{{ __("From") }}:</span>
          <FormControl
            v-model="fromEmail"
            type="select"
            variant="ghost"
            class="w-full"
            :placeholder="__('')"
            :options="from"
          />
        </div>
        <div class="mx-6 md:mx-5 flex items-center gap-2 border-y py-2.5">
          <span class="text-p-xs text-ink-gray-4">{{ __("To") }}:</span>
          <EmailMultiSelect
            v-model="toEmailsClone"
            class="flex-1"
            scope="contact"
            variant="ghost"
            allow-custom-email
            copy-on-click
            :validate="validateEmailWithZod"
            :custom-email-label="__('Add to recipients')"
          />
          <div class="flex gap-1.5">
            <Button
              :label="__('Cc')"
              variant="ghost"
              :class="[
                cc || showCC
                  ? '!bg-surface-gray-4 hover:bg-surface-gray-3'
                  : '!text-ink-gray-4',
              ]"
              @click="toggleCC()"
            />
            <Button
              :label="__('Bcc')"
              variant="ghost"
              :class="[
                bcc || showBCC
                  ? '!bg-surface-gray-4 hover:bg-surface-gray-3'
                  : '!text-ink-gray-4',
              ]"
              @click="toggleBCC()"
            />
          </div>
        </div>
        <div
          v-if="showCC || cc"
          class="mx-5 flex items-center gap-2 py-2.5"
          :class="cc || showCC ? 'border-b' : ''"
        >
          <span class="text-xs text-ink-gray-4">{{ __("Cc:") }}</span>
          <EmailMultiSelect
            ref="ccInput"
            v-model="ccEmailsClone"
            class="flex-1"
            scope="contact"
            variant="ghost"
            allow-custom-email
            copy-on-click
            :validate="validateEmailWithZod"
            :custom-email-label="__('Add to recipients')"
          />
        </div>
        <div
          v-if="showBCC || bcc"
          class="mx-5 flex items-center gap-2 py-2.5"
          :class="bcc || showBCC ? 'border-b' : ''"
        >
          <span class="text-xs text-ink-gray-4">{{ __("Bcc:") }}</span>
          <EmailMultiSelect
            ref="bccInput"
            v-model="bccEmailsClone"
            class="flex-1"
            scope="contact"
            variant="ghost"
            allow-custom-email
            copy-on-click
            :validate="validateEmailWithZod"
            :custom-email-label="__('Add to recipients')"
          />
        </div>

        <!-- Editor content + quoted reply -->
        <div class="overflow-y-auto min-h-[7rem] max-h-[30vh] flex flex-col">
          <!--
            Once a translation is drafted the editor holds the customer's
            language, which the agent cannot read. So the text they wrote stays
            visible above it and both blocks are labelled with their language:
            nobody sends a language they cannot read without seeing what it was
            made from.
          -->
          <div
            v-if="outboundDraft"
            class="mx-6 md:mx-5 mt-3 rounded border border-outline-gray-2 bg-surface-gray-1 p-2 text-p-xs text-ink-gray-6"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="font-medium text-ink-gray-7">
                {{ __("Din text") }} ({{
                  languageName(outboundDraft.source_language)
                }})
              </span>
              <button
                class="text-ink-gray-5 underline"
                @click="discardOutboundDraft()"
              >
                {{ __("Ångra översättningen") }}
              </button>
            </div>
            <div class="mt-1 whitespace-pre-wrap">
              {{ outboundDraft.original_text }}
            </div>
            <div class="mt-2 font-medium text-ink-gray-7">
              {{ __("Skickas på") }} {{ languageName(outboundDraft.target_language) }}
            </div>
          </div>
          <div class="flex-1">
            <EditorContent
              :class="[
                'prose-sm max-w-full mx-6 md:mx-5 py-3',
                getFontFamily(newEmail),
                '[&_p.reply-to-content]:hidden',
              ]"
            />
          </div>
          <div
            v-if="quotedContent"
            class="replied-content mx-6 md:mx-5 mb-2 mt-auto"
          >
            <label class="collapse" for="quoted-toggle">...</label>
            <input
              id="quoted-toggle"
              class="replyCollapser"
              type="checkbox"
              :checked="isQuoteExpanded"
            />
            <div
              ref="quotedContentRef"
              contenteditable="true"
              class="prose !max-w-full mx-1 my-2 border-s-4 border-outline-gray-2 ps-4 text-sm focus:outline-none"
              @input="onQuotedInput"
            />
          </div>
        </div>

        <!-- What the AI suggestion rests on, under the text it composed. Shown
             only when there is something to show: a completion request rests on
             the order and not on the library, and an empty heading would read
             as a claim of grounding nobody made. -->
        <div
          v-if="replySources.length"
          class="mx-5 my-2 text-p-xs text-ink-gray-4"
        >
          {{ __("Svaret bygger på") }}
          <template
            v-for="(source, index) in replySources"
            :key="source.article"
          >
            <span v-if="index">, </span>
            <!-- A deleted article gets its title but no link: the page it would
                 open is gone, and "no longer in the library" tells the agent
                 this is not the same thing as a renamed one. -->
            <template v-if="source.missing">
              <span>{{ source.title }}</span>
              <span> ({{ __("finns inte längre i biblioteket") }})</span>
            </template>
            <template v-else>
              <router-link
                class="underline hover:text-ink-gray-6"
                target="_blank"
                :to="{ name: 'Article', params: { articleId: source.article } }"
                >{{ source.title }}</router-link
              >
              <!-- The article moved after the draft was written, so the agent
                   has to re-read it before approving. -->
              <span v-if="source.stale">
                ({{ __("ändrad sedan utkastet skrevs") }})</span
              >
            </template>
          </template>
          <!-- The check against the library did not answer. Said out loud, in
               the same grey, so a clean list is never mistaken for a checked
               one. -->
          <span v-if="sourcesCheck === 'failed'">
            &mdash; {{ __("kunde inte kontrolleras mot biblioteket") }}</span
          >
        </div>
        <!-- Attachments -->
        <AttachmentList
          class="px-5 my-2"
          :attachments="attachments"
          @remove="removeAttachment"
        />
        <!-- Saved reply actions, applied once the reply is sent -->
        <SavedReplyActions
          ref="savedReplyActionsRef"
          class="mx-5 my-2"
          :ticket-id="ticketId"
          :doctype="doctype"
        />
        <!-- Fixed Menu -->
        <div
          class="flex justify-between overflow-scroll px-4 py-2.5 items-center border-t"
        >
          <div class="flex items-center overflow-x-auto w-[60%]">
            <div class="inline-flex items-center gap-1.5 p-1">
              <FileUploader
                :upload-args="{
                  doctype: doctype,
                  docname: ticketId,
                  private: true,
                }"
                @success="
                  (f) => {
                    attachments.push(f);
                  }
                "
              >
                <template #default="{ openFileSelector, uploading }">
                  {{ void (isUploading = uploading) }}
                  <Tooltip :text="__('Attach file')">
                    <button
                      class="flex rounded p-1 text-ink-gray-8 transition-colors focus-within:ring-0 hover:bg-surface-gray-3"
                      @click="openFileSelector()"
                      :disabled="uploading"
                    >
                      <LoadingIndicator v-if="uploading" class="h-4 w-4" />
                      <AttachmentIcon
                        v-else
                        class="h-4 w-4"
                        style="stroke-width: 1.5 !important"
                      />
                    </button>
                  </Tooltip>
                </template>
              </FileUploader>
              <Tooltip :text="__('Saved replies')">
                <button
                  class="flex rounded p-1 text-ink-gray-8 transition-colors focus-within:ring-0 hover:bg-surface-gray-3"
                  @click="showSavedRepliesSelectorModal = true"
                >
                  <ZapIcon class="h-4 w-4" />
                </button>
              </Tooltip>
              <Tooltip :text="__('AI-förslag')">
                <button
                  class="flex items-center gap-1 rounded p-1 text-ink-gray-8 transition-colors focus-within:ring-0 hover:bg-surface-gray-3 disabled:opacity-50"
                  :disabled="suggestReply.loading"
                  @click="suggestReply.submit()"
                >
                  <LoadingIndicator v-if="suggestReply.loading" class="h-4 w-4" />
                  <SparklesIcon v-else class="h-4 w-4" />
                  <span class="text-p-xs">{{ __("AI-förslag") }}</span>
                </button>
              </Tooltip>
              <!-- Only offered when the customer reads another language -->
              <Tooltip
                v-if="needsOutboundTranslation"
                :text="__('Svara på kundens språk')"
              >
                <button
                  class="flex items-center gap-1 rounded p-1 text-ink-gray-8 transition-colors focus-within:ring-0 hover:bg-surface-gray-3 disabled:opacity-50"
                  :disabled="
                    draftOutbound.loading ||
                    Boolean(outboundDraft) ||
                    isContentEmpty(newEmail)
                  "
                  @click="draftOutbound.submit()"
                >
                  <LoadingIndicator
                    v-if="draftOutbound.loading"
                    class="h-4 w-4"
                  />
                  <LanguagesIcon v-else class="h-4 w-4" />
                  <span class="text-p-xs">{{
                    __("Svara på kundens språk")
                  }}</span>
                </button>
              </Tooltip>
              <div class="h-4 w-[2px] border-s ml-1" />
            </div>
            <EditorFixedMenu :items="fullToolbar" />
          </div>
          <div class="flex items-center justify-end gap-x-2 sm:mt-0 w-[40%]">
            <Button label="Discard" @click="handleDiscard" />
            <Button
              variant="solid"
              :disabled="isDisabled"
              :loading="sendMail.loading || replyTranslated.loading"
              :label="label"
              @click="
                () => {
                  submitMail();
                }
              "
            />
            <!-- Send and move the ticket to a chosen status in one click -->
            <Dropdown
              v-if="doctype === 'HD Ticket' && resolveOptions.length"
              :options="resolveOptions"
              placement="top-end"
            >
              <Button
                variant="subtle"
                :disabled="isDisabled || sendAndSetStatus.loading"
                :loading="sendAndSetStatus.loading"
                :label="__('Skicka & markera klar')"
              >
                <template #suffix>
                  <ChevronDownIcon class="h-4 w-4" />
                </template>
              </Button>
            </Dropdown>
          </div>
        </div>
      </div>
    </template>
  </Editor>
  <SavedRepliesSelectorModal
    v-model="showSavedRepliesSelectorModal"
    :doctype="doctype"
    @apply="applySavedReplies"
    :ticketId="ticketId"
  />
</template>

<script setup lang="ts">
import { AttachmentList, SavedRepliesSelectorModal } from "@/components";
import { buildEditorExtensions, fullToolbar } from "@/components/editor/config";
import EmailMultiSelect from "@/components/EmailMultiSelect.vue";
import { createDialog } from "@/components/dialogs";
import { AttachmentIcon } from "@/components/icons";
import SavedReplyActions from "@/components/SavedReplyActions/SavedReplyActions.vue";
import { useTyping } from "@/composables/realtime";
import { getUserEmailInfo } from "@/composables/useUserEmailInfo";
import { replyComposer } from "@/pages/ticket/modalStates";
import { useAuthStore } from "@/stores/auth";
import { useTicketStatusStore } from "@/stores/ticketStatus";
import { HDTicketStatus } from "@/types/doctypes";
import { __ } from "@/translation";
import { RenderedSavedReply } from "@/types";
import {
  getFontFamily,
  htmlToText,
  isContentEmpty,
  removeAttachmentFromServer,
  uploadFunction,
  validateEmailWithZod,
} from "@/utils";
import { useStorage } from "@vueuse/core";
import {
  Dropdown,
  FileUploader,
  LoadingIndicator,
  Tooltip,
  call,
  createResource,
  toast,
} from "frappe-ui";
import { Editor, EditorContent, EditorFixedMenu } from "frappe-ui/editor";
import { useOnboarding } from "frappe-ui/frappe";
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import ChevronDownIcon from "~icons/lucide/chevron-down";
import SparklesIcon from "~icons/lucide/sparkles";
import ZapIcon from "~icons/lucide/zap";
import LanguagesIcon from "~icons/lucide/languages";

// ─── Props & Emits ────────────────────────────────────────────
const props = defineProps({
  ticketId: {
    type: String,
    default: null,
  },
  placeholder: {
    type: String,
    default: null,
  },
  label: {
    type: String,
    default: "Send",
  },
  editable: {
    type: Boolean,
    default: true,
  },
  doctype: {
    type: String,
    default: "HD Ticket",
  },
  toEmails: {
    type: Array,
    default: () => [],
  },
  ccEmails: {
    type: Array,
    default: () => [],
  },
  bccEmails: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(["submit", "discard"]);

const { updateOnboardingStep } = useOnboarding("helpdesk");
const { isManager } = useAuthStore();
const { onUserType, cleanup } = useTyping(props.ticketId);

const extensions = buildEditorExtensions();

const editorRef = ref(null);
const editor = computed(() => editorRef.value?.editor);

function focusEditorAtStart() {
  setTimeout(() => {
    editorRef.value?.editor?.commands?.focus("start");
  }, 0);
}

const cachedEmail = useStorage<null | string>(
  "emailBoxContent" + props.ticketId,
  null
);

const newEmail = ref<null | string>(cachedEmail.value);

const emailSignature = ref<string | null>(null);

function isOnlySignature(content: string | null) {
  if (!content || !emailSignature.value) return false;
  return htmlToText(content) === htmlToText(emailSignature.value);
}

const userResource = getUserEmailInfo();

const quotedContent = useStorage<null | string>(
  "quotedEmailBoxContent" + props.ticketId,
  null
);
const quotedContentRef = ref<HTMLElement | null>(null);
const isQuoteExpanded = ref(false);

function onQuotedInput() {
  const el = quotedContentRef.value;
  if (!el) return;
  quotedContent.value = el.innerHTML || null;
}

const toEmailsClone = ref([...props.toEmails]);
const ccEmailsClone = ref([...props.ccEmails]);
const bccEmailsClone = ref([...props.bccEmails]);
const showCC = ref(false);
const showBCC = ref(false);
const cc = computed(() => (ccEmailsClone.value?.length ? true : false));
const bcc = computed(() => (bccEmailsClone.value?.length ? true : false));
const ccInput = ref(null);
const bccInput = ref(null);

function toggleCC() {
  showCC.value = !showCC.value;
  showCC.value &&
    nextTick(() => {
      ccInput.value.setFocus();
    });
}

function toggleBCC() {
  showBCC.value = !showBCC.value;
  showBCC.value &&
    nextTick(() => {
      bccInput.value.setFocus();
    });
}

const fromEmail = useStorage<string | "">("from-email", "");

const outgoingEmails = computed<{ email_account: string; email_id: string }[]>(
  () => userResource.data?.outgoing_emails ?? []
);

// selected mail from the outgoing emails list
const selectedFromEmail = computed(() =>
  outgoingEmails.value.find((e) => e.email_id === fromEmail.value)
);

const from = computed(() => {
  if (!outgoingEmails.value.length) return [];
  if (
    outgoingEmails.value.length === 1 &&
    outgoingEmails.value[0].email_id === userResource.data?.email
  )
    return [];
  return outgoingEmails.value.map((e) => ({
    label: e.email_account + " <" + e.email_id + ">",
    value: e.email_id,
  }));
});

const hasMultipleSenders = computed(() => (from?.value.length ?? 0) > 1);

const attachments = ref([]);
const isUploading = ref(false);

async function removeAttachment(attachment) {
  attachments.value = attachments.value.filter((a) => a !== attachment);
  await removeAttachmentFromServer(attachment.name);
}

const showSavedRepliesSelectorModal = ref(false);
const savedReplyActionsRef = ref<InstanceType<typeof SavedReplyActions>>();

/** A reply is only replaced when another one is already applied. */
function applySavedReplies(reply: RenderedSavedReply) {
  const staged = savedReplyActionsRef.value?.stagedSummary();
  if (!staged) {
    insertSavedReply(reply);
    return;
  }
  createDialog({
    title: __("Replace saved reply"),
    message: __(
      'Applying "{0}" discards the reply you have now, along with its {1} action(s).',
      [reply.title, staged.count]
    ),
    actions: [
      { label: __("Cancel") },
      {
        label: __("Replace"),
        variant: "solid",
        onClick: ({ close }: { close: () => void }) => {
          replaceSavedReply(reply);
          close();
        },
      },
    ],
  });
}

/** First reply of a draft: added to whatever the agent has already written. */
function insertSavedReply(reply: RenderedSavedReply) {
  const textEditor = editorRef.value?.editor;
  if (!textEditor) return;
  textEditor.chain().focus("start").insertContent(reply.message).run();
  savedReplyActionsRef.value?.add(reply);
}

/** Confirmed replace: the new reply's body and actions stand alone. */
function replaceSavedReply(reply: RenderedSavedReply) {
  // The suggestion is gone with the text; a saved reply rests on no articles.
  clearReplySources();
  newEmail.value = reply.message + (emailSignature.value ?? "");
  savedReplyActionsRef.value?.add(reply);
  focusEditorAtStart();
}

/** The payload both the plain send and "send & mark done" hand to the server. */
function replyArgs() {
  return {
    attachments: attachments.value.map((x) => x.name),
    from_email: selectedFromEmail.value,
    to: toEmailsClone.value.join(","),
    cc: ccEmailsClone.value?.join(","),
    bcc: bccEmailsClone.value?.join(","),
    message:
      newEmail.value +
      (quotedContentRef.value
        ? `<p class="reply-to-content"></p><blockquote>${quotedContentRef.value.innerHTML}</blockquote>`
        : ""),
  };
}

function onReplySent() {
  savedReplyActionsRef.value?.submit();
  resetState();
  emit("submit");

  if (isManager) {
    updateOnboardingStep("reply_on_ticket");
  }
}

const sendMail = createResource({
  url: "run_doc_method",
  makeParams: () => ({
    dt: props.doctype,
    dn: props.ticketId,
    method: "reply_via_agent",
    args: replyArgs(),
  }),
  onSuccess: onReplySent,
  debounce: 300,
});

// "Skicka & markera klar": the same reply, followed by the status the agent
// picked. The server refuses a status the site has not defined.
const sendAndSetStatus = createResource({
  url: "helpdesk.api.ticket.reply_and_set_status",
  makeParams: (status: string) => ({
    ticket_id: props.ticketId,
    status,
    ...replyArgs(),
  }),
  onSuccess: onReplySent,
});

const ticketStatusStore = useTicketStatusStore();

// Resolved statuses first (the default "done"), then the Paused ones such as
// "Väntar på kund"; the order within each group is the admin's.
const resolveOptions = computed(() => {
  const statuses: HDTicketStatus[] =
    ticketStatusStore.statuses.data?.filter((s: HDTicketStatus) => s.enabled) ??
    [];
  const pick = (category: string) =>
    statuses.filter((s) => s.category === category);
  return [...pick("Resolved"), ...pick("Paused")].map((s) => ({
    label: __(s.label_agent),
    onClick: () => submitMailWithStatus(s.label_agent),
  }));
});

/**
 * Ask the backend for the one draft this ticket needs (a completion request
 * for a half-stated order, or a knowledge reply) and put it in the editor
 * above whatever the agent has written. Nothing is sent by this button.
 */
type ReplySource = {
  article: string;
  title: string;
  stale?: boolean;
  /** The approved version in the library now; null once the article is gone. */
  current_version?: number | null;
  /** Set by the check: the article no longer exists, so no link is offered. */
  missing?: boolean;
};

/** The articles the suggestion in the editor rests on, empty until one arrives. */
const replySources = ref<ReplySource[]>([]);

/**
 * What the library check said about `replySources`: nothing yet, an answer,
 * or no answer. Only "checked" means the titles have been compared with the
 * library; the interface says so for "failed" rather than looking clean.
 */
const sourcesCheck = ref<"unchecked" | "checked" | "failed">("unchecked");

/**
 * The draft the citation currently belongs to. A check answers for one draft;
 * if the citation has been cleared or replaced by the time the answer lands,
 * the answer is for a draft nobody is looking at and is dropped.
 */
let citedDraftId: string | null = null;

/** The citation goes with the text it came from: emptied whenever that is. */
function clearReplySources() {
  citedDraftId = null;
  replySources.value = [];
  sourcesCheck.value = "unchecked";
}

/**
 * Ask for the staleness of the cited articles, which only the server can know:
 * it compares the version the draft read with the one approved now. Failing
 * this leaves the titles standing with a note that they were not checked.
 */
function markStaleSources(draftId: string) {
  citedDraftId = draftId;
  sourcesCheck.value = "unchecked";
  call("helpdesk.api.ai_reply.get_reply_sources", { draft_id: draftId })
    .then((resolved: ReplySource[]) => {
      if (citedDraftId !== draftId) return;
      // We asked about titles we know the draft has; an empty answer has not
      // checked them.
      if (!Array.isArray(resolved) || !resolved.length) {
        sourcesCheck.value = "failed";
        return;
      }
      replySources.value = resolved.map((source) => ({
        ...source,
        missing: source.current_version == null,
      }));
      sourcesCheck.value = "checked";
    })
    .catch(() => {
      if (citedDraftId !== draftId) return;
      sourcesCheck.value = "failed";
    });
}

const suggestReply = createResource({
  url: "helpdesk.api.ai_reply.suggest_reply",
  makeParams: () => ({ ticket_id: props.ticketId }),
  onSuccess: (draft: {
    name?: string;
    body?: string | null;
    reason?: string;
    sources?: ReplySource[];
  }) => {
    // A newer draft than any check still in flight: the older answer is dropped.
    clearReplySources();
    replySources.value = Array.isArray(draft?.sources) ? draft.sources : [];
    if (draft?.name && replySources.value.length) markStaleSources(draft.name);
    if (!draft?.body) {
      toast.warning(draft?.reason || __("Inget AI-förslag finns för det här ärendet."));
      return;
    }
    const textEditor = editorRef.value?.editor;
    const html = draft.body.includes("<") ? draft.body : `<p>${draft.body}</p>`;
    if (textEditor) {
      textEditor.chain().focus("start").insertContent(html).run();
    } else {
      newEmail.value = html + (newEmail.value ?? "");
    }
  },
  onError: (error: any) => {
    toast.error(error?.messages?.[0] || __("Kunde inte hämta AI-förslag."));
  },
});

// ---------------------------------------------------------------------------
// The answer in the customer's language
// ---------------------------------------------------------------------------

/** Language codes the desk meets often; anything else is shown as its code. */
const LANGUAGE_NAMES: Record<string, string> = {
  sv: "svenska",
  no: "norska",
  nb: "norska",
  nn: "norska",
  da: "danska",
  fi: "finska",
  en: "engelska",
  de: "tyska",
  fr: "franska",
  es: "spanska",
  nl: "nederländska",
  pl: "polska",
};

function languageName(code?: string | null): string {
  if (!code) return __("okänt språk");
  return LANGUAGE_NAMES[code.toLowerCase()] ?? code;
}

type OutboundDraft = {
  translation: string | null;
  original_text: string;
  translated_text: string;
  source_language: string | null;
  target_language: string | null;
};

/** The drafted translation, unreviewed and unsent until the agent presses send. */
const outboundDraft = ref<OutboundDraft | null>(null);

// Which language this ticket is answered in, and where that reading came from.
// Asked once when the ticket loads and kept, because the button's presence is
// the whole point: no reading, no button.
const replyLanguage = createResource({
  url: "helpdesk.api.translation.reply_language",
  makeParams: () => ({ ticket_id: props.ticketId }),
  auto: false,
});

const needsOutboundTranslation = computed(
  () =>
    props.doctype === "HD Ticket" &&
    Boolean(replyLanguage.data?.needs_translation)
);

/**
 * The agent's text as the model should see it: plain, without the signature.
 * The signature is the agent's, not the customer's: it is never translated
 * and never sent to the model. A held draft keeps its own original text, so
 * a second drafting cannot translate the translation.
 */
function draftSourceText(): string {
  if (outboundDraft.value) return outboundDraft.value.original_text;
  const body = htmlToText(newEmail.value ?? "").trim();
  const signature = emailSignature.value
    ? htmlToText(emailSignature.value).trim()
    : "";
  if (signature && body.endsWith(signature)) {
    return body.slice(0, body.length - signature.length).trim();
  }
  return body;
}

/** The translated body with the signature once: only when it is not already there. */
function withSignature(html: string): string {
  if (!emailSignature.value) return html;
  const signature = htmlToText(emailSignature.value).trim();
  if (signature && htmlToText(html).trim().endsWith(signature)) return html;
  return html + emailSignature.value;
}

/** The text the last drafting was asked for, kept as the draft's original. */
const draftRequestText = ref("");

/**
 * Ask for the answer in the customer's language. The server records it as an
 * unreviewed Outbound `HD Message Translation`; nothing leaves the building
 * here. The translation goes into the editor and the agent's own text stays
 * above it.
 */
const draftOutbound = createResource({
  url: "helpdesk.api.translation.draft_outbound",
  makeParams: () => {
    draftRequestText.value = draftSourceText();
    return { ticket_id: props.ticketId, text: draftRequestText.value };
  },
  onSuccess: (draft: any) => {
    const translated = draft?.translated_text;
    if (!draft?.needs_translation || !translated) {
      toast.info(__("Kunden läser samma språk som du skriver."));
      return;
    }
    outboundDraft.value = {
      translation: draft.translation ?? draft.name ?? null,
      original_text: draftRequestText.value,
      translated_text: translated,
      source_language: draft.source_language ?? null,
      target_language: draft.target_language ?? replyLanguage.data?.language,
    };
    const html = translated.includes("<")
      ? translated
      : `<p>${translated}</p>`;
    newEmail.value = withSignature(html);
    focusEditorAtStart();
  },
  onError: (error: any) => {
    toast.error(
      error?.messages?.[0] || __("Kunde inte översätta svaret.")
    );
  },
});

function discardOutboundDraft() {
  outboundDraft.value = null;
}

/** The status "Skicka & markera klar" picked, cleared once the send is answered. */
const pendingStatus = ref<string | null>(null);

/**
 * Sends the drafted translation: the press of this button *is* the review the
 * Wave 4 rule asks for. A failure is reported and the draft kept: the server
 * may already have sent the mail, so sending it again from here could double
 * it or send the untranslated text. The agent decides what happens next.
 */
const replyTranslated = createResource({
  url: "helpdesk.api.ticket.reply_translated",
  makeParams: (status?: string) => ({
    ticket_id: props.ticketId,
    translation_id: outboundDraft.value?.translation,
    status,
    ...replyArgs(),
  }),
  onSuccess: () => {
    outboundDraft.value = null;
    pendingStatus.value = null;
    onReplySent();
  },
  onError: (error: any) => {
    pendingStatus.value = null;
    toast.error(
      error?.messages?.[0] || __("Det översatta svaret kunde inte skickas.")
    );
  },
});


const label = computed(() => (sendMail.loading ? "Sending..." : props.label));

const isDisabled = computed(
  () =>
    (isContentEmpty(newEmail.value) && isContentEmpty(quotedContent.value)) ||
    sendMail.loading ||
    isUploading.value
);

function canSend(): boolean {
  if (isContentEmpty(newEmail.value) && isContentEmpty(quotedContent.value)) {
    return false;
  }
  if (
    !toEmailsClone.value.length &&
    !ccEmailsClone.value.length &&
    !bccEmailsClone.value.length
  ) {
    toast.warning(
      "Email has no recipients. Please add at least one recipient (To, Cc, or Bcc) before sending."
    );
    return false;
  }
  return true;
}

/**
 * LANG-08: on a ticket answered in another language, the first press of Send
 * translates instead of sending. The agent then reads what the customer will
 * actually receive and presses Send again — and that second press is the
 * review Wave 22 defined, unchanged.
 *
 * A ticket answered in the working language never comes here, and neither
 * does an agent who already used «Svara på kundens språk»: they have a draft,
 * and translating a translation is how a reply loses its meaning.
 */
function draftBeforeSending(): boolean {
  if (!needsOutboundTranslation.value) return false;
  if (outboundDraft.value?.translation) return false;
  if (draftOutbound.loading) return true;
  toast.info(
    __("Svaret översätts till kundens språk. Läs det och skicka igen.")
  );
  draftOutbound.submit();
  return true;
}

function submitMail() {
  if (!canSend()) return false;
  if (draftBeforeSending()) return;
  // A drafted translation is sent through the door that reviews and sends in
  // the same motion; without one this is the ordinary reply.
  if (outboundDraft.value?.translation) {
    replyTranslated.submit();
    return;
  }
  sendMail.submit();
}

function submitMailWithStatus(status: string) {
  if (!canSend()) return false;
  if (draftBeforeSending()) return;
  if (outboundDraft.value?.translation) {
    pendingStatus.value = status;
    replyTranslated.submit(status);
    return;
  }
  sendAndSetStatus.submit(status);
}

function getInitialContent() {
  return emailSignature.value ? emailSignature.value : "<p></p>";
}

function addToReply(
  body: string,
  toEmails: string[],
  ccEmails: string[],
  bccEmails: string[]
) {
  toEmailsClone.value = toEmails;
  ccEmailsClone.value = ccEmails;
  bccEmailsClone.value = bccEmails;

  // Plain-text emails (e.g. Thunderbird) have no HTML tags, so their newlines/spacing
  // thus lose formatting when added to replied content
  const doc = new DOMParser().parseFromString(body, "text/html");
  if (doc.body.children.length === 0) {
    body = `<div style="white-space: pre-wrap; line-height: 1.5">${doc.body.innerHTML}</div>`;
  }

  if (body !== quotedContent.value) {
    //trigger change for watch when replied to body data is different from current quoted content
    quotedContent.value = null;
    isQuoteExpanded.value = false;
    nextTick(() => {
      quotedContent.value = body;
    });
  }

  clearReplySources();
  nextTick(() => {
    newEmail.value = getInitialContent();
  });
  focusEditorAtStart();
}

// Staged actions are left to `submit()`, which unstages them either way
function resetState() {
  outboundDraft.value = null;
  clearReplySources();
  newEmail.value = emailSignature.value ? emailSignature.value : null;
  attachments.value = [];
  quotedContent.value = null;
  isQuoteExpanded.value = false;
  focusEditorAtStart();
}

function handleDiscard() {
  outboundDraft.value = null;
  clearReplySources();
  attachments.value = [];
  savedReplyActionsRef.value?.clear();
  newEmail.value = getInitialContent();
  quotedContent.value = null;
  ccEmailsClone.value = [];
  bccEmailsClone.value = [];
  showCC.value = false;
  showBCC.value = false;
  isQuoteExpanded.value = false;

  focusEditorAtStart();
  emit("discard");
}

function handleSelectAll(e: KeyboardEvent) {
  const active = document.activeElement;
  const editorContext = editorRef.value?.editor;
  const editorDom = editorContext?.view?.dom as HTMLElement | undefined;
  const quotedEl = quotedContentRef.value;
  const sel = window.getSelection();
  if (!sel || !editorDom) return;
  if (!editorDom.contains(active) && !(quotedEl && quotedEl.contains(active))) {
    return;
  }
  e.preventDefault();
  editorContext?.commands.selectAll();
  sel.removeAllRanges();
  const range = document.createRange();

  if (quotedEl) {
    range.setStartBefore(editorDom);
    range.setEndAfter(quotedEl);
  } else {
    range.selectNodeContents(editorDom);
  }
  sel.addRange(range);
}

function handleDelete(e: KeyboardEvent) {
  const sel = window.getSelection();
  const quotedEl = quotedContentRef.value;
  const editorDom = editorRef.value?.editor?.view?.dom as
    | HTMLElement
    | undefined;

  if (!sel || sel.isCollapsed || !quotedEl || !editorDom) return;

  const isSelectingEntireEditor = sel.containsNode(editorDom, true);
  const isSelectingEntireQuote = sel.containsNode(quotedEl, true);

  if (isSelectingEntireEditor && isSelectingEntireQuote) {
    e.preventDefault();

    editorRef.value?.editor?.commands?.clearContent();
    clearReplySources();
    newEmail.value = null;
    quotedContent.value = null;

    sel.removeAllRanges();
  }
}

function handleKeydown(e: KeyboardEvent) {
  const key = e.key.toLowerCase();

  if ((e.metaKey || e.ctrlKey) && key === "a") {
    isQuoteExpanded.value = true;
    handleSelectAll(e);
    return;
  }

  if (key === "backspace" || key === "delete") {
    handleDelete(e);
    return;
  }
}

watch(newEmail, (newValue, oldValue) => {
  if (newValue !== oldValue && newValue) {
    onUserType();
  }
  cachedEmail.value = isOnlySignature(newValue) ? null : newValue;
});

watch(quotedContent, (newVal, oldVal) => {
  if (!oldVal && newVal) {
    nextTick(() => {
      if (quotedContentRef.value) {
        quotedContentRef.value.innerHTML = newVal;
      }
    });
  }
});

watch(
  () => userResource.data,
  (data: { email_signature?: string } | null) => {
    if (!data?.email_signature) return;
    emailSignature.value = `<br>${data.email_signature}`;
    if (isOnlySignature(cachedEmail.value)) {
      cachedEmail.value = null;
    }
    if (isContentEmpty(newEmail.value) && !quotedContent.value) {
      newEmail.value = emailSignature.value;
      focusEditorAtStart();
    }
  },
  { immediate: true }
);

watch(
  from,
  (fromOptions) => {
    if (!fromOptions.find((f) => f.value === fromEmail.value)) {
      fromEmail.value = fromOptions.length ? fromOptions[0].value : "";
    }
  },
  { immediate: true }
);

onMounted(() => {
  // Published for the command palette, which cannot reach `editorRef` from
  // module scope. See modalStates.ts.
  replyComposer.value = applySavedReplies;
  if (props.doctype === "HD Ticket" && props.ticketId) {
    replyLanguage.fetch();
  }
  if (quotedContent.value) {
    nextTick(() => {
      if (quotedContentRef.value) {
        quotedContentRef.value.innerHTML = quotedContent.value;
      }
    });
  }
});

onBeforeUnmount(() => {
  replyComposer.value = null;
  cleanup();
});

defineExpose({
  addToReply,
  editor,
  submitMail,
});
</script>
