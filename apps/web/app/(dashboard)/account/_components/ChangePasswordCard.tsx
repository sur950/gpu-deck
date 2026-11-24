'use client';

import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { PasswordInput } from '@/components/shared/PasswordInput';
import { LoadingSpinner } from '@/components/shared/LoadingSkeleton';
import { useQueryClient } from '@tanstack/react-query';
import { useChangePassword } from '@/hooks/use-auth';
import { accountKeys } from '@/hooks/use-account';

const schema = z
  .object({
    currentPassword: z.string().min(1, 'Enter your current password'),
    newPassword: z.string().min(12, 'Use at least 12 characters'),
    confirmPassword: z.string(),
  })
  .refine((v) => v.newPassword === v.confirmPassword, {
    message: 'Passwords do not match',
    path: ['confirmPassword'],
  })
  .refine((v) => v.newPassword !== v.currentPassword, {
    message: 'Choose a password you have not used',
    path: ['newPassword'],
  });

type FormValues = z.infer<typeof schema>;

/** react-hook-form + zod + ui/form pattern. */
export function ChangePasswordCard() {
  const mutation = useChangePassword();
  const queryClient = useQueryClient();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { currentPassword: '', newPassword: '', confirmPassword: '' },
  });

  async function onSubmit(values: FormValues) {
    try {
      await mutation.mutateAsync({ currentPassword: values.currentPassword, newPassword: values.newPassword });
      form.reset();
      // The backend revokes every other session on a password change.
      void queryClient.invalidateQueries({ queryKey: accountKeys.all });
    } catch (err) {
      form.setError('root', { message: err instanceof Error ? err.message : 'Could not change the password' });
    }
  }

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>Signs out every other session; this one stays signed in.</CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="currentPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Current password</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="current-password" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="newPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>New password</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="new-password" {...field} />
                  </FormControl>
                  <FormDescription>At least 12 characters.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="confirmPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Confirm new password</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="new-password" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {form.formState.errors.root && (
              <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {form.formState.errors.root.message}
              </p>
            )}
            <div className="flex justify-end">
              <Button type="submit" size="sm" className="rounded-sm" disabled={mutation.isPending}>
                {mutation.isPending ? <LoadingSpinner className="h-4 w-4" /> : 'Update password'}
              </Button>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}
