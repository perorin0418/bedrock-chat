import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import ListPageLayout from '../layouts/ListPageLayout';
import Button from '../components/Button';
import usePendingUsers from '../hooks/usePendingUsers';
import useUserApproval from '../hooks/useUserApproval';
import useSnackbar from '../hooks/useSnackbar';

const AdminUserApprovalPage: React.FC = () => {
  const { t } = useTranslation();
  const { pendingUsers, isLoading, mutate } = usePendingUsers();
  const { approveUser, isUpdating } = useUserApproval();
  const { open } = useSnackbar();

  const onClickApprove = useCallback(
    async (userId: string) => {
      const succeeded = await approveUser(userId);
      if (succeeded) {
        mutate();
      } else {
        open(t('admin.userApproval.error.failApprove'));
      }
    },
    [approveUser, mutate, open, t]
  );

  return (
    <ListPageLayout
      pageTitle={t('admin.userApproval.label.pageTitle')}
      pageTitleHelp={t('admin.userApproval.help.overview')}
      isLoading={isLoading}
      isEmpty={pendingUsers?.length === 0}
      emptyMessage={t('admin.userApproval.label.noPendingUsers')}>
      <div className="flex flex-col gap-2">
        {pendingUsers?.map((user) => (
          <div
            key={user.id}
            className="flex items-center justify-between rounded border border-gray p-2">
            <div>{user.email}</div>
            <Button
              loading={isUpdating}
              onClick={() => {
                onClickApprove(user.id);
              }}>
              {t('admin.userApproval.button.approve')}
            </Button>
          </div>
        ))}
      </div>
    </ListPageLayout>
  );
};

export default AdminUserApprovalPage;
