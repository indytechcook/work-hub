/** Shared UI/RPC validation. The ledger independently enforces this boundary. */
export function workNameError(value:string):string {
 if(/[\p{Cc}\p{Zl}\p{Zp}]/u.test(value))return 'Use a single-line work name without control characters.';
 const length=[...value.trim()].length;
 return length<1||length>200?'Work name must be 1–200 characters.':'';
}
