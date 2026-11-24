/** UI types for the admin account page (sessions of the seeded admin). */

export interface AdminSession {
  id: string;
  ipAddress: string | null;
  userAgent: string | null;
  createdAt: string;
  lastActiveAt: string | null;
  expiresAt: string;
  isCurrent: boolean;
}

export interface AdminProfile {
  id: string;
  username: string;
  displayName: string | null;
  createdAt: string | null;
  lastLoginAt: string | null;
  lastLoginIp: string | null;
  passwordChangedAt: string | null;
}
