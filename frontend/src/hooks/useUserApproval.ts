import { useState } from 'react';
import useAdminApi from './useAdminApi';

const useUserApproval = () => {
  const adminApi = useAdminApi();
  const [isUpdating, setIsUpdating] = useState(false);

  /**
   * Approve a user pending admin approval
   */
  const approveUser = async (userId: string) => {
    setIsUpdating(true);
    try {
      await adminApi.approveUser(userId);
      return true;
    } catch (error) {
      console.error('Failed to approve user:', error);
      return false;
    } finally {
      setIsUpdating(false);
    }
  };

  return {
    approveUser,
    isUpdating,
  };
};

export default useUserApproval;
