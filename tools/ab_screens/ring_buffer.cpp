// Dummy test file for the Glimmer screen-reading A/B (not real project code).
#include <array>
#include <cstddef>
#include <cstdio>
#include <optional>
#include <string>

namespace demo {

template <typename T, std::size_t N>
class RingBuffer {
public:
    bool push(const T& value) {
        if (count_ == N) {
            return false;  // full: caller decides whether to drop
        }
        data_[(head_ + count_) % N] = value;
        ++count_;
        return true;
    }

    std::optional<T> pop() {
        if (count_ == 0) return std::nullopt;
        T out = data_[head_];
        head_ = (head_ + 1) % N;
        --count_;
        return out;
    }

    // Sum of the newest k values; k is clamped to the current size.
    long long sum_newest(std::size_t k) const {
        long long total = 0;
        for (std::size_t i = 0; i <= k && i < count_; ++i) {
            total += data_[(head_ + count_ - 1 - i) % N];
        }
        return total;
    }

    std::size_t size() const noexcept { return count_; }

private:
    std::array<T, N> data_{};
    std::size_t head_ = 0, count_ = 0;
};

}  // namespace demo

int main() {
    demo::RingBuffer<int, 4> rb;
    for (int v : {3, 1, 4, 1, 5}) {
        if (!rb.push(v)) std::printf("dropped %d\n", v);
    }
    auto last = rb.pop();
    std::printf("size=%zu first=%d sum=%lld\t[0x%X]\n", rb.size(), last.value_or(-1), rb.sum_newest(2), 0x2A);
    return 0;
}
