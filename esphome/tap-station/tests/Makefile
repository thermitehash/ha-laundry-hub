CXX ?= g++
CXXFLAGS ?= -std=c++17 -Wall -Wextra -Werror -pedantic -O1 -fsanitize=address,undefined
SAN_LDFLAGS ?= -fsanitize=address,undefined

test: test_st25r_i2c_proto
	./test_st25r_i2c_proto

test_st25r_i2c_proto: test_st25r_i2c_proto.cpp ../components/st25r_i2c/st25r_i2c_proto.h
	$(CXX) $(CXXFLAGS) -o $@ test_st25r_i2c_proto.cpp $(SAN_LDFLAGS)

clean:
	rm -f test_st25r_i2c_proto

.PHONY: test clean
