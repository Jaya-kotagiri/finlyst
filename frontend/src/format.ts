// Presentation only: avoid floating-point conversion of authoritative money strings.
export function money(value: string | null): string {
  if (value === null) return 'Not available'
  const negative = value.startsWith('-')
  const [whole, fraction] = value.replace(/^-/, '').split('.')
  return `${negative ? '−' : ''}₹${BigInt(whole).toLocaleString('en-IN')}.${fraction}`
}

export function date(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
    .format(new Date(value + 'T00:00:00Z'))
}

export function importDate(value: string): string {
  return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
    .format(new Date(value))
}
