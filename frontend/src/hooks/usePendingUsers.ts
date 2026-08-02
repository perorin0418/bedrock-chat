import useAdminApi from './useAdminApi';

const usePendingUsers = () => {
  const { listPendingUsers } = useAdminApi();

  const { data, isLoading, mutate } = listPendingUsers();

  return {
    pendingUsers: data,
    isLoading,
    mutate,
  };
};

export default usePendingUsers;
