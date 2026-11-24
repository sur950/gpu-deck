/**
 * Admin API paths used by the shell. Relative to NEXT_PUBLIC_API_URL
 * (`…/api/v1`). Feature modules keep their own paths in `lib/api/<feature>.ts`;
 * only the auth/session surface the shell depends on lives here, so one edit
 * follows the contract in `libs/contracts/openapi/admin.yaml`.
 */
export const AUTH_ENDPOINTS = {
  login: '/auth/login',
  logout: '/auth/logout',
  me: '/auth/me',
  changePassword: '/auth/change-password',
} as const;
