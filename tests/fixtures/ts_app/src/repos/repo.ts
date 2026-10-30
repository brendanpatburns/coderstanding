export interface Finder<T> {
  all(): T[];
}

export interface Store<T> extends Finder<T> {
  clear(): void;
}

export abstract class Repo<T> implements Store<T> {
  protected items: T[] = [];

  all(): T[] {
    return this.items;
  }

  clear(): void {
    this.items = [];
  }
}
