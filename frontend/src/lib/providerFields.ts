import type { LLMProviderField } from "./types";

type FieldValues = Readonly<Record<string, string>> | undefined;

/** A field's value as the form shows it: a dropdown left unset shows its first option. */
export function providerFieldValue(field: LLMProviderField, values: FieldValues): string {
  const value = values?.[field.name] ?? "";
  return value || (field.choices[0]?.value ?? "");
}

/** Whether a field applies, given the values of the provider's other fields. */
export function isProviderFieldShown(
  field: LLMProviderField,
  fields: readonly LLMProviderField[],
  values: FieldValues,
): boolean {
  const condition = field.shown_when;
  if (!condition) return true;
  const controller = fields.find((f) => f.name === condition.field);
  const current = controller ? providerFieldValue(controller, values) : (values?.[condition.field] ?? "");
  return current === condition.value;
}

/** The fields to show, in order. */
export function shownProviderFields(
  fields: readonly LLMProviderField[],
  values: FieldValues,
): LLMProviderField[] {
  return fields.filter((field) => isProviderFieldShown(field, fields, values));
}
