<template>
  <div
    :id="`communication-${name}`"
    v-bind="$attrs"
    class="grow cursor-pointer bg-surface-base rounded-md text-base leading-6 transition-all duration-300 ease-in-out border border-outline-gray-2"
  >
    <div
      class="flex items-center justify-between gap-2"
      :class="isMobileView && 'items-start'"
    >
      <!-- email design for mobile -->
      <div v-if="isMobileView" class="flex items-center gap-2 text-sm">
        <div class="leading-tight">
          <p>{{ sender.full_name || "Guest" }}</p>
          <Tooltip :text="dateFormat(creation, dateTooltipFormat)">
            <p class="text-xs md:text-sm text-ink-gray-5">
              {{ timeAgo(creation) }}
            </p>
          </Tooltip>
          <p class="sm:flex hidden text-sm text-ink-gray-5" v-if="sender.name">
            {{ "<" + sender.name + ">" }}
          </p>
        </div>
      </div>
      <!-- email design for desktop -->
      <div v-else class="flex items-center gap-1">
        <span>{{ sender.full_name || "Guest" }}</span>
        <span
          class="sm:flex hidden text-sm text-ink-gray-5"
          v-if="sender.name"
          >{{ "<" + sender.name + ">" }}</span
        >
      </div>

      <div class="flex gap-2 items-center">
        <div class="gap-0.5 flex items-center">
          <!-- QUEUE-02: the demo runs with the outgoing queue suspended, so a
               composed mail can sit unsent for days. Where the thread shows the
               delivery status it says so, and a message the queue never saw is
               left exactly as it was. -->
          <Tooltip
            v-if="isHeld && !ticket?.doc?.via_customer_portal"
            :text="
              __(
                'Meddelandet är skrivet och ligger i utskickskön. Det skickas för hand från Frappe-desken.'
              )
            "
          >
            <Badge
              :label="__('Väntar på utskick')"
              variant="subtle"
              theme="orange"
              class="me-1.5"
            />
          </Tooltip>
          <Badge
            v-else-if="status.label && !ticket?.doc?.via_customer_portal"
            :label="__(status.label)"
            variant="subtle"
            :theme="status.color"
            class="me-1.5"
          />
          <Tooltip
            :text="dateFormat(creation, dateTooltipFormat)"
            v-if="!isMobileView"
          >
            <p class="text-xs md:text-sm text-ink-gray-5">
              {{ timeAgo(creation) }}
            </p>
          </Tooltip>
        </div>
        <div class="flex items-center gap-1">
          <Button :tooltip="__('Reply')" variant="ghost" @click="reply">
            <template #icon>
              <ReplyIcon class="text-ink-gray-7" />
            </template>
          </Button>
          <Button :tooltip="__('Reply All')" variant="ghost" @click="replyAll">
            <template #icon>
              <ReplyAllIcon class="text-ink-gray-7" />
            </template>
          </Button>
          <Dropdown
            v-if="showSplitOption"
            :placement="'right'"
            :options="[
              {
                label: 'Split Ticket',
                icon: LucideSplit,
                onClick: () => (showSplitModal = true),
              },
            ]"
          >
            <Button
              icon="lucide-more-horizontal"
              class="!text-ink-gray-7"
              variant="ghost"
            />
          </Dropdown>
        </div>
      </div>
    </div>
    <!-- <div class="text-sm leading-5 text-ink-gray-5">
      {{ subject }}
    </div> -->
    <div class="text-p-sm text-ink-gray-5">
      <template
        v-for="(val, label) in { To: to, cc: cc, bcc: bcc }"
        :key="label"
      >
        <span v-if="val" class="me-1.5">
          <span class="me-1 text-ink-gray-7">{{ label }}:</span>
          <span> {{ normalizeAndFilter(val).join(", ") }}</span>
        </span>
      </template>
    </div>
    <div class="border-0 border-t my-3 border-outline-elevation-2 !-mx-3" />
    <!-- LANG-01/02/03: the message reads in the language the agent works in,
         and the words the customer actually sent stay one click away. Where
         there is no translation this renders exactly what it always did. -->
    <!-- LANG-06: a sent answer is shown as it was sent. The Communication is
         the mail the customer got — formatting, signature, quoted history —
         so EmailContent renders it whenever the sent words are shown; only the
         toggle to the agent's original swaps in the plain original_text. -->
    <EmailContent
      v-if="!translation || (isOutbound && !showOriginal)"
      :content="content"
    />
    <p v-else class="whitespace-pre-line break-words text-ink-gray-8">
      {{ showOriginal ? translation.original_text : translation.translated_text }}
    </p>
    <div
      v-if="translation"
      class="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-gray-4"
    >
      <LanguagesIcon class="h-3.5 w-3.5 shrink-0" />
      <span>
        {{
          showOriginal
            ? (isOutbound
                ? (translation.source_language
                    ? __("Handläggarens original ({0})").replace(
                        "{0}",
                        translation.source_language
                      )
                    : __("Handläggarens original"))
                : __("Kundens egna ord ({0})").replace(
                    "{0}",
                    translation.source_language
                  ))
            : (isOutbound
                ? (translation.target_language
                    ? __("Skickat på {0}").replace(
                        "{0}",
                        translation.target_language
                      )
                    : __("Skickat på kundens språk"))
                : __("Maskinöversatt från {0}").replace(
                    "{0}",
                    translation.source_language
                  ))
        }}
      </span>
      <span v-if="!showOriginal && translation.model_version">
        · {{ translation.model_version }}
      </span>
      <Button
        class="ms-auto shrink-0"
        variant="ghost"
        size="sm"
        :label="showOriginal ? __('Visa översättning') : __('Visa original')"
        @click.stop="showOriginal = !showOriginal"
      />
    </div>
    <div class="flex flex-wrap gap-2">
      <AttachmentItem
        v-for="a in attachments"
        :key="a.file_url"
        :label="a.file_name"
        :url="a.file_url"
      />
    </div>
  </div>
  <TicketSplitModal
    v-model="showSplitModal"
    :ticket_id="name"
    :communication_id="name"
  />
</template>

<script setup lang="ts">
import { AttachmentItem } from "@/components";
import { useScreenSize } from "@/composables/screen";
import { useTicketDelivery } from "@/composables/useTicketDelivery";
import { useTicketTranslations } from "@/composables/useTicketTranslations";
import { useAuthStore } from "@/stores/auth";
import { TicketSymbol } from "@/types";
import { dateFormat, dateTooltipFormat, timeAgo } from "@/utils";
import { Dropdown } from "frappe-ui";
import { storeToRefs } from "pinia";
import { computed, inject, ref } from "vue";
import LucideSplit from "~icons/lucide/split";
import LanguagesIcon from "~icons/lucide/languages";
import { ReplyAllIcon, ReplyIcon } from "./icons";
import TicketSplitModal from "./ticket/TicketSplitModal.vue";

const props = defineProps({
  activity: {
    type: Object,
    required: true,
  },
  showSplitOption: {
    type: Boolean,
    default: false,
  },
});

const {
  sender,
  to,
  cc,
  bcc,
  creation,
  subject,
  attachments,
  content,
  name,
  deliveryStatus,
  sentOrReceived,
} = props.activity;

const emit = defineEmits(["reply"]);
const ticket = inject(TicketSymbol)!;

// `.value` in script scope: the injected ticket is a ComputedRef, which the
// template unwraps and this does not. Reading it without unwrapping is what
// made the triage panel fetch nothing at all for a fortnight.
const { forMessage, forOutboundMessage } = useTicketTranslations(
  computed(() => ticket.value?.doc?.name)
);
// The customer's message and the desk's reply get the same band, but they
// are different rows. The row is chosen by the message's own direction — a
// Sent message takes the outbound row, a received one the inbound row — so the
// row rendered and the caption describing it are always the same row.
const translation = computed(() =>
  sentOrReceived === "Sent" ? forOutboundMessage(name) : forMessage(name)
);
const isOutbound = computed(() => translation.value?.direction === "Outbound");
const { forMessage: deliveryForMessage } = useTicketDelivery(
  computed(() => ticket.value?.doc?.name)
);
const isHeld = computed(() => Boolean(deliveryForMessage(name)?.held));

const showOriginal = ref(false);

const auth = storeToRefs(useAuthStore());

const { isMobileView } = useScreenSize();

const showSplitModal = ref(false);

const status = computed(() => {
  let _status = deliveryStatus;
  let indicator_color = "red";
  if (["Sent", "Clicked"].includes(_status)) {
    indicator_color = "green";
  } else if (["Sending", "Scheduled"].includes(_status)) {
    indicator_color = "orange";
  } else if (["Opened", "Read"].includes(_status)) {
    indicator_color = "blue";
  } else if (_status == "Error") {
    indicator_color = "red";
  }
  return { label: _status, color: indicator_color };
});

const normalizeAndFilter = (
  field: string | string[],
  valuesToExclude: string[] = []
) => {
  let arr = [];
  let current = "";
  let inQuotes = false;
  if (typeof field === "string") {
    for (let char of field) {
      if (char === '"') {
        inQuotes = !inQuotes;
        current += char;
      } else if (char === "," && !inQuotes) {
        arr.push(current.trim());
        current = "";
      } else {
        current += char;
      }
    }
    if (current) arr.push(current.trim());
  } else {
    arr = field || [];
  }
  return arr.filter(Boolean).filter((item) => !valuesToExclude.includes(item));
};

const reply = () => {
  const user = auth.user.value;
  emit("reply", {
    content: content,
    to: user === sender.name ? to : sender.name,
  });
};

const replyAll = () => {
  const user = auth.user.value;
  const exclude = [user, sender.name];
  const filteredTo = normalizeAndFilter(to, exclude);
  const filteredCc = normalizeAndFilter(cc, exclude);
  const filteredBcc = normalizeAndFilter(bcc, exclude);

  let _to, _cc, _bcc;

  if (user === sender.name) {
    // User is the sender, reply to all original recipients
    _to = filteredTo.join(", ");
    _cc = filteredCc;
    _bcc = filteredBcc;
  } else {
    // User is a recipient, reply to sender with all other recipients in cc
    _to = sender.name;
    _cc = [...filteredTo, ...filteredCc];
    _bcc = filteredBcc;
  }

  emit("reply", {
    content: content,
    to: _to,
    cc: _cc.filter(Boolean),
    bcc: _bcc.filter(Boolean),
  });
};

// TODO: Implement reply functionality using this way instead of emit drillup
// function reply(email, reply_all = false) {
//   emailBox.toggleEmailBox();
//   let editor = emailBox.editor;
//   let message = email.content;
//   let recipients = sender.name;
//   editor.toEmails = [email.sender];
//   editor.cc = editor.bcc = false;
//   editor.ccEmails = [];
//   editor.bccEmails = [];
//   console.log(recipients);

//   if (!email.subject.startsWith("Re:")) {
//     editor.subject = `Re: ${email.subject}`;
//   } else {
//     editor.subject = email.subject;
//   }

//   if (reply_all) {
//     let cc = email.cc?.split(",").map((r) => r.trim());
//     let bcc = email.bcc?.split(",").map((r) => r.trim());

//     if (cc?.length) {
//       recipients = recipients.filter((r) => !cc?.includes(r));
//       cc.push(...recipients);
//     } else {
//       cc = recipients;
//     }

//     editor.cc = cc ? true : false;
//     editor.bcc = bcc ? true : false;

//     editor.ccEmails = cc;
//     editor.bccEmails = bcc;
//   }

//   let repliedMessage = `<blockquote>${message}</blockquote>`;

//   editor.editor
//     .chain()
//     .clearContent()
//     .insertContent("<p>.</p>")
//     .updateAttributes("paragraph", { class: "reply-to-content" })
//     .insertContent(repliedMessage)
//     .focus("all")
//     .insertContentAt(0, { type: "paragraph" })
//     .focus("start")
//     .run();
// }
</script>

<style>
.email-content {
  max-width: 100%;
}
.email-content > * {
  display: flex;
  flex-direction: column;
  flex-wrap: nowrap;
}
</style>
