<template>
  <div class="flex flex-col gap-2">
    <div
      v-for="(engine, index) in chain"
      :key="index"
      class="flex items-center gap-2"
    >
      <span class="text-sm text-ink-gray-5 w-4 text-end">{{ index + 1 }}</span>
      <FormControl
        class="flex-1"
        type="select"
        :model-value="engine"
        :options="options.map((name) => ({ label: name, value: name }))"
        @update:model-value="replace(index, $event)"
      />
      <!-- Arrows rather than dragging: the order is short, and an arrow works
           on a keyboard and on a touch screen without a drag library. -->
      <Button
        icon="arrow-up"
        theme="gray"
        variant="ghost"
        :disabled="index === 0"
        :label="__('Flytta upp')"
        @click="move(index, -1)"
      />
      <Button
        icon="arrow-down"
        theme="gray"
        variant="ghost"
        :disabled="index === chain.length - 1"
        :label="__('Flytta ner')"
        @click="move(index, 1)"
      />
      <Button
        icon="x"
        theme="gray"
        variant="ghost"
        :label="__('Ta bort')"
        @click="remove(index)"
      />
    </div>
    <div>
      <Button
        :label="__('Lägg till motor')"
        theme="gray"
        variant="subtle"
        icon-left="plus"
        :disabled="!options.length"
        @click="add()"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { __ } from "@/translation";
import { Button, FormControl } from "frappe-ui";

const props = defineProps<{ chain: string[]; options: string[] }>();
const emit = defineEmits<{ (event: "update", chain: string[]): void }>();

/** Every edit emits a fresh array, so the parent owns the chain it stores. */
function edit(change: (next: string[]) => void) {
  const next = props.chain.slice();
  change(next);
  emit("update", next);
}

function replace(index: number, engine: string) {
  edit((next) => {
    next[index] = engine;
  });
}

function move(index: number, step: number) {
  const target = index + step;
  if (target < 0 || target >= props.chain.length) return;
  edit((next) => {
    const [row] = next.splice(index, 1);
    next.splice(target, 0, row);
  });
}

function remove(index: number) {
  edit((next) => next.splice(index, 1));
}

function add() {
  edit((next) => next.push(props.options[0]));
}
</script>
