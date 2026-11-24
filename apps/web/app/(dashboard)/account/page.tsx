import type { Metadata } from 'next';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ChangePasswordCard } from '@/app/(dashboard)/account/_components/ChangePasswordCard';
import { ProfileCard } from '@/app/(dashboard)/account/_components/profile-card';
import { SessionsCard } from '@/app/(dashboard)/account/_components/sessions-card';

export const metadata: Metadata = { title: 'Account' };

/** Reached from the profile menu. Profile, password and the admin's sessions. */
export default function AccountPage() {
  return (
    <PageContainer>
      <PageHeader title="Account" description="The seeded admin account: password and signed-in sessions." />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <div id="password" className="scroll-mt-20">
            <ChangePasswordCard />
          </div>
          <SessionsCard />
        </div>
        <div>
          <ProfileCard />
        </div>
      </div>
    </PageContainer>
  );
}
