export function titleCase(s: string): string {
  return s
    .split(/\s+/)
    .map((w) => (w.length ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w))
    .join(" ");
}

export function formatPriceAED(value: number): string {
  return `AED ${value.toLocaleString("en-US")}`;
}
