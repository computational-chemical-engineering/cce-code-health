template <class T> int f(T x) {
  if constexpr (A<T>) { return 1; } else if constexpr (B<T>) { return 2; } else { return x ? 3 : 4; }
}
int g(int x) {
#if defined(A)
  if (x) { x++; }
#elif 0
  while (x) x--; while (x) x--;
#else
  if (x > 1 && x < 9) { x--; }
#endif
  return x;
}
void h(int c) {
#ifdef M
  if (false) {
#else
  if (c > 1) {
#endif
    c++;
  }
}
void k() { for (int i=0;i<2;++i) for (int j=0;j<2;++j) for (int l=0;l<2;++l) if (i) c++; }
void q(int a) { if (a) if (a>1) if (a>2) if (a>3) a++; }
void s(View a, View b, int n) {
  const double dt = 0.1;
  Kokkos::parallel_for("k", n, [a, &b, c = 2 * n](const int i) { a(i) = b(i) + c; });
  Kokkos::parallel_for("k2", n, KOKKOS_LAMBDA(const int i) { if (i) a(i) = dt * b(i) + n; });
}
