export type User = {
  id: string;
  name: string;
  email: string;
};
export type UserGroup = {
  name: string;
  description: string;
};

export type SearchedUser =
  | ({
      type: 'user';
    } & User)
  | ({
      type: 'group';
    } & UserGroup);

export type SearchUsersRequest = {
  prefix: string;
};
export type SearchUsersResponse = User[];

export type SearchUserGroupsRequest = {
  prefix: string;
};
export type SearchUserGroupsResponse = UseGroup[];

export type GetUserResponse = User;

export type ListPendingUsersResponse = User[];
